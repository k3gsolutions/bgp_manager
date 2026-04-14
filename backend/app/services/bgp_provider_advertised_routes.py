"""
Lista de prefixos BGP advertidos a um peer **Operadora** via SSH (Huawei VRP).
Usa só o comando base ``advertised-routes`` (coluna Path/Ogn) — sem ``detail`` por prefixo.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from ..activity_log import emit
from .bgp_export_lookup import (
    _is_valid_output,
    _peer_address_family,
    _route_not_found,
    _send,
)


PAGE_SIZE = 20
# Máximo de NLRI apresentados na UI; acima disso avisa e trunca (paginação só sobre estas linhas).
MAX_DISPLAY_ROUTES = 200

_RE_TOTAL_ROUTES = re.compile(
    r"Total\s+Number\s+of\s+Routes\s*:\s*(\d+)",
    re.IGNORECASE,
)


def _advertised_list_cmds(peer_ip: str, vrf_name: str) -> list[str]:
    """
    VRP: lista de NLRI enviados ao peer — NE8000 / VPN-Instance.

    - Global: ``display bgp routing-table peer <ip> advertised-routes``
    - VRF IPv4 (preferido): ``display bgp vpnv4 vpn-instance <VRF> routing-table peer <ip> advertised-routes``
    - VRF IPv6 (preferido): ``display bgp vpnv6 vpn-instance <VRF> routing-table peer <ip> advertised-routes``
    Depois tenta sintaxes alternativas por versão de VRP.
    """
    vrf = (vrf_name or "").strip()
    fam = _peer_address_family(peer_ip)
    if not vrf:
        if fam == "ipv6":
            return [
                f"display bgp ipv6 routing-table peer {peer_ip} advertised-routes",
                f"display bgp routing-table peer {peer_ip} advertised-routes",
            ]
        return [f"display bgp routing-table peer {peer_ip} advertised-routes"]
    if fam == "ipv6":
        return [
            f"display bgp vpnv6 vpn-instance {vrf} routing-table peer {peer_ip} advertised-routes",
            f"display bgp ipv6 routing-table vpn-instance {vrf} peer {peer_ip} advertised-routes",
            f"display bgp routing-table vpn-instance {vrf} peer {peer_ip} advertised-routes",
        ]
    return [
        f"display bgp vpnv4 vpn-instance {vrf} routing-table peer {peer_ip} advertised-routes",
        f"display bgp routing-table vpn-instance {vrf} peer {peer_ip} advertised-routes",
    ]


def _path_from_attr_tail(parts: list[str]) -> str | None:
    """
    Path/Ogn à direita: último token costuma ser ``266208i``; ASNs intermediários só dígitos.
    Remove um ``0`` inicial residual (PrefVal/MED) quando ainda há AS-Path à direita.
    """
    if not parts:
        return None
    last_as = -1
    for j in range(len(parts) - 1, -1, -1):
        if re.match(r"^\d+[a-zA-Z?]$", parts[j]):
            last_as = j
            break
    if last_as < 0:
        return None
    first_as = last_as
    j = last_as - 1
    while j >= 0 and re.match(r"^\d+$", parts[j]):
        first_as = j
        j -= 1
    seg = parts[first_as : last_as + 1]
    while seg and seg[0] == "0" and len(seg) > 1:
        seg = seg[1:]
    return " ".join(seg) if seg else None


def _parse_reported_total(text: str) -> int | None:
    m = _RE_TOTAL_ROUTES.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


_RE_NETWORK_PREFIXLEN = re.compile(
    r"Network\s*:\s*([0-9a-fA-F:]+)\s+.*?PrefixLen\s*:\s*(\d+)",
    re.IGNORECASE,
)
_RE_PATH_OGN = re.compile(r"Path/Ogn\s*:\s*(.+)$", re.IGNORECASE)


def _normalize_path_ogn_tail(s: str) -> str:
    """Ex.: ``268707 264196  i`` → ``268707 264196i`` (indicador de origem na Huawei)."""
    t = (s or "").strip()
    t = re.sub(r"\s+i\s*$", "i", t, flags=re.IGNORECASE)
    return t


def _parse_huawei_network_prefixlen_path_ogn(text: str) -> list[dict[str, str]]:
    """
    Formato multi-linha (comum em IPv6 / vpn-instance): blocos com
    ``Network : <addr> ... PrefixLen : N`` seguidos de ``Path/Ogn : ...``.
    """
    rows: list[dict[str, str]] = []
    pending: tuple[str, int] | None = None
    for raw in text.splitlines():
        t = raw.rstrip()
        st = t.strip()
        if not st or st.startswith("BGP ") or "Local router ID" in st:
            continue
        if _RE_TOTAL_ROUTES.match(st):
            continue

        m_po = _RE_PATH_OGN.search(t)
        if m_po and pending:
            addr, plen = pending
            pending = None
            try:
                prefix = str(ipaddress.ip_network(f"{addr}/{plen}", strict=False))
            except ValueError:
                continue
            as_path = _normalize_path_ogn_tail(m_po.group(1))
            rows.append({"prefix": prefix, "as_path": as_path})
            continue

        m_net = _RE_NETWORK_PREFIXLEN.search(t)
        if m_net:
            addr = m_net.group(1).strip()
            try:
                plen = int(m_net.group(2))
            except ValueError:
                continue
            pending = (addr, plen)

    return rows


def _parse_classic_advertised_table_lines(text: str) -> list[dict[str, str]]:
    """Tabela clássica (uma linha por rota, prefixo já em notação CIDR)."""
    rows: list[dict[str, str]] = []
    path_col_idx: int | None = None
    for line in text.splitlines():
        raw = line.rstrip()
        t = raw.strip()
        if not t or t.startswith("BGP ") or "Local router ID" in t:
            continue
        if _RE_TOTAL_ROUTES.match(t):
            continue
        if "Network" in t and "NextHop" in t and "Path/Ogn" in t:
            # Usa a posição real da coluna Path/Ogn para evitar "capturar"
            # MED/LocPrf/PrefVal como se fossem AS-PATH.
            try:
                path_col_idx = raw.index("Path/Ogn")
            except ValueError:
                path_col_idx = None
            continue
        if t.startswith("---") or ("Network" in t and "NextHop" in t):
            continue
        if re.match(r"^Status\s+codes", t, re.I):
            continue
        if re.match(r"^RPKI\s+validation", t, re.I):
            continue
        if re.match(r"^\s*Origin\s*:", t, re.I):
            continue
        # Evita confundir com o formato Network : / PrefixLen :
        if _RE_NETWORK_PREFIXLEN.search(t) and "PrefixLen" in t:
            continue

        m = re.match(
            r"^\s*([\*\>dDrRsShxiaSs\?]+)\s+(\S+)\s+(\S+)\s+(.*)$",
            raw,
        )
        if not m:
            continue
        prefix = m.group(2).strip()
        rest = (m.group(4) or "").strip()
        if "/" not in prefix:
            continue
        try:
            ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue

        as_path = ""
        if path_col_idx is not None and len(raw) > path_col_idx:
            col_tail = raw[path_col_idx:].strip()
            if col_tail.lower().startswith("path/ogn"):
                col_tail = col_tail[8:].strip()
            as_path = _normalize_path_ogn_tail(col_tail)
        if not as_path:
            parts = rest.split()
            as_path = _path_from_attr_tail(parts) or ""
        rows.append({"prefix": prefix, "as_path": as_path})
    return rows


def _parse_advertised_routes_table(text: str) -> list[dict[str, str]]:
    """
    Extrai prefixo + AS-Path (Path/Ogn) de ``advertised-routes`` / ``received-routes``.

    - Formato **bloco** (IPv6 Huawei): ``Network :`` + ``PrefixLen :`` + ``Path/Ogn :``
    - Formato **clássico**: uma linha por rota com prefixo CIDR.
    """
    block = _parse_huawei_network_prefixlen_path_ogn(text)
    if block:
        return block
    return _parse_classic_advertised_table_lines(text)


def run_huawei_provider_peer_advertised_routes(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    vendor: str,
    peer_ip: str,
    vrf_name: str,
    offset: int,
    fetch_all: bool,
    log: list[str],
) -> dict[str, Any]:
    from netmiko import ConnectHandler

    if (vendor or "").strip().lower() != "huawei":
        return {"error": "vendor", "message": "Apenas Huawei (VRP).", "items": [], "log": log}

    offset = max(0, int(offset or 0))
    device_types = ["huawei_vrp", "huawei"]
    last_err: Exception | None = None
    conn = None
    for device_type in device_types:
        try:
            conn = ConnectHandler(
                device_type=device_type,
                host=host,
                port=port,
                username=username,
                password=password,
                timeout=120,
                auth_timeout=60,
                banner_timeout=60,
                conn_timeout=20,
                fast_cli=False,
            )
            emit(log, f"SSH OK ({device_type}) — advertised-routes para peer {peer_ip}")
            break
        except Exception as e:
            last_err = e
            emit(log, f"SSH falhou ({device_type}): {e!s}")
            conn = None
    if conn is None:
        return {
            "error": "ssh",
            "message": str(last_err) if last_err else "Falha SSH",
            "items": [],
            "log": log,
        }

    try:
        raw_list = ""
        for cmd in _advertised_list_cmds(peer_ip, vrf_name):
            raw_list = _send(conn, cmd, log, timeout=120)
            if _is_valid_output(raw_list) and not _route_not_found(raw_list):
                break
        if not _is_valid_output(raw_list) or _route_not_found(raw_list):
            return {
                "error": "empty",
                "message": "Sem saída válida de advertised-routes (sessão ou tabela vazia).",
                "items": [],
                "total": 0,
                "reported_total": None,
                "offset": offset,
                "page_size": PAGE_SIZE,
                "has_more": False,
                "too_many": False,
                "capped": False,
                "full_total": None,
                "peer_ip": peer_ip,
                "vrf_name": (vrf_name or "").strip(),
                "log": log,
            }

        reported_total = _parse_reported_total(raw_list)
        all_rows = _parse_advertised_routes_table(raw_list)
        n_parsed = len(all_rows)

        if reported_total is not None and n_parsed != reported_total:
            emit(
                log,
                f"Aviso: «Total Number of Routes: {reported_total}» vs {n_parsed} linhas parseadas na tabela.",
            )

        full_total = n_parsed
        capped = full_total > MAX_DISPLAY_ROUTES
        if capped:
            emit(
                log,
                f"Aviso: {full_total} rotas na tabela advertised-routes; listagem limitada a {MAX_DISPLAY_ROUTES} nesta UI.",
            )
            rows_for_ui = all_rows[:MAX_DISPLAY_ROUTES]
            cap_message = (
                f"Foram detetadas {full_total} rotas anunciadas a este peer; "
                f"por performance só as primeiras {MAX_DISPLAY_ROUTES} são listadas aqui (paginação de {PAGE_SIZE} em {PAGE_SIZE}). "
                "Para o inventário completo use a CLI no equipamento."
            )
        else:
            rows_for_ui = all_rows
            cap_message = None

        n_display = len(rows_for_ui)
        if fetch_all:
            slice_rows = rows_for_ui
            page_offset = 0
            has_more = False
        else:
            slice_rows = rows_for_ui[offset : offset + PAGE_SIZE]
            page_offset = offset
            has_more = offset + len(slice_rows) < n_display

        return {
            "too_many": False,
            "message": cap_message,
            "items": slice_rows,
            "total": n_display,
            "reported_total": reported_total,
            "offset": page_offset,
            "page_size": PAGE_SIZE,
            "has_more": has_more,
            "capped": capped,
            "full_total": full_total if capped else None,
            "peer_ip": peer_ip,
            "vrf_name": (vrf_name or "").strip(),
            "log": log,
        }
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
