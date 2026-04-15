"""
Investigação de prefixo BGP via SSH (Huawei VRP / NE8000).
Segue o fluxo do guia K3G · BGP Prefix Investigation:

  Passo 1 — display bgp routing-table {PREFIX/LEN}
  Passo 2 — display bgp routing-table {PREFIX/LEN} detail  (todos os paths)
  Passo 3 — display bgp peer {FROM_PEER_IP} verbose        (tipo/descr do peer)
  Passo 4 — communities (Standard / Ext / Large)           (extraídas do detalhe)
  Passo 5 — display bgp routing-table peer {OP} advertised-routes | include
  Passo 6 — advertised-routes {PREFIX} detail por peer (todos os papéis; IPv4/IPv6)
  Passo 7 — ASN regex: regular-expression ^{ASN}$ / _{ASN}_
"""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Any

from ..activity_log import emit
from .inforr_communities import filter_inforr_standard_communities


def _operator_peers_by_ip(operator_peers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    m: dict[str, list[dict[str, Any]]] = {}
    for p in operator_peers or []:
        pip = p.get("peer_ip")
        if not pip:
            continue
        m.setdefault(str(pip), []).append(p)
    return m


def _pick_operator_peer(ops_by_ip: dict[str, list[dict[str, Any]]], pip: str) -> dict[str, Any]:
    """Mesmo IP em Principal + VRF: prioriza a linha da instância principal (vrf vazio)."""
    lst = ops_by_ip.get(str(pip)) or []
    if not lst:
        return {}
    principals = [x for x in lst if not (str(x.get("vrf_name") or "").strip())]
    if principals:
        return principals[0]
    return lst[0]


# ── Helpers de análise de input ─────────────────────────────────────────────

def _vendor_netmiko(vendor: str) -> str:
    return {"Huawei": "huawei_vrp", "Cisco": "cisco_ios", "Juniper": "juniper_junos"}.get(
        vendor or "", "huawei_vrp"
    )


def _try_ipv4(q: str) -> tuple[str, int, bool] | None:
    s = q.strip()
    if "/" in s:
        try:
            net = ipaddress.ip_network(s, strict=False)
            if isinstance(net, ipaddress.IPv4Network):
                return str(net.network_address), net.prefixlen, True
        except ValueError:
            return None
    try:
        ipaddress.IPv4Address(s)
        return s, 32, False
    except ValueError:
        return None


def _try_asn(q: str) -> str | None:
    s = q.strip().upper().removeprefix("AS").strip()
    if s.isdigit() and 1 <= int(s) <= 4_294_967_295:
        return s
    return None


# ── Parsers de saída VRP ────────────────────────────────────────────────────

_RE_AS_PATH = re.compile(
    r"(?:AS-path|AS-Path|AS_PATH|As-path)\s*(?:[：:]|\s)\s*([^,\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_LOCAL_PREF = re.compile(r"(?:Local-Pref|Local Preference|LocalPref)\s*[：:]\s*(\d+)", re.IGNORECASE)
_RE_MED = re.compile(r"\bMED\s*[：:]\s*(\d+)", re.IGNORECASE)
_RE_ORIGIN = re.compile(r"\bOrigin\s*[：:]?\s*(igp|egp|\?)", re.IGNORECASE)
_RE_NEXTHOP = re.compile(r"(?:Nexthop|NextHop|Next-hop)\s*[：:]\s*([\d.]+)", re.IGNORECASE)
_RE_FROM = re.compile(r"\bFrom\s*[：:]\s*([\d.]+)", re.IGNORECASE)
_RE_COMMUNITY_STD = re.compile(r"^\s*Community\s*[：:]?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_COMMUNITY_EXT = re.compile(r"^\s*(?:Ext-Community|ExtCommunity)\s*[：:]?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_COMMUNITY_LARGE = re.compile(r"^\s*(?:Large-Community|LargeCommunity)\s*[：:]?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RE_STATUS_ROUTE = re.compile(r"^\s*(\*>?)\s+([\d.]+(?:/\d+)?)\s", re.MULTILINE)


def _first(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _parse_community_tokens(raw_line: str) -> list[str]:
    """Extrai tokens do tipo ASN:VAL, <ASN:VAL>, ou NO-EXPORT, NO-ADVERTISE de uma linha."""
    tokens: list[str] = []
    # <ASN:VAL> format
    for m in re.finditer(r"<([^>]+)>", raw_line):
        v = m.group(1).strip()
        if v and v not in tokens:
            tokens.append(v)
    # ASN:VAL format
    for m in re.finditer(r"\b(\d+:\d+)\b", raw_line):
        v = m.group(1)
        if v not in tokens:
            tokens.append(v)
    # Named communities
    for kw in ("NO-EXPORT", "NO-ADVERTISE", "NO-EXPORT-SUBCONFED", "NOPEER"):
        if kw.lower() in raw_line.lower() and kw not in tokens:
            tokens.append(kw)
    return tokens


def _parse_all_communities(text: str) -> tuple[list[str], list[str], list[str]]:
    """Retorna (standard, extended, large)."""
    std: list[str] = []
    ext: list[str] = []
    large: list[str] = []

    for m in _RE_COMMUNITY_STD.finditer(text):
        for t in _parse_community_tokens(m.group(1)):
            if t not in std:
                std.append(t)
    for m in _RE_COMMUNITY_EXT.finditer(text):
        raw = m.group(1).strip()
        for tok in re.split(r"\s+|,", raw):
            tok = tok.strip("<>").strip()
            if tok and tok not in ext:
                ext.append(tok)
    for m in _RE_COMMUNITY_LARGE.finditer(text):
        raw = m.group(1).strip()
        for tok in re.split(r"\s+|,", raw):
            tok = tok.strip("<>").strip()
            if tok and tok not in large:
                large.append(tok)

    return std, ext, large


def _parse_detail_block(text: str) -> dict[str, Any]:
    """Extrai atributos principais de um bloco `detail` do VRP."""
    as_path = _first(_RE_AS_PATH, text)
    lp_s = _first(_RE_LOCAL_PREF, text)
    med_s = _first(_RE_MED, text)
    origin = _first(_RE_ORIGIN, text)
    nexthop = _first(_RE_NEXTHOP, text)
    from_ip = _first(_RE_FROM, text)
    std, ext, large = _parse_all_communities(text)

    nums = re.findall(r"\b(\d+)\b", as_path or "")
    prepend = any(a == b for a, b in zip(nums, nums[1:]))

    return {
        "as_path": as_path,
        "as_path_numbers": nums,
        "origin": (origin or "").lower() or None,
        "local_pref": int(lp_s) if lp_s else None,
        "med": int(med_s) if med_s else None,
        "nexthop": nexthop,
        "from_peer_ip": from_ip,
        "communities": filter_inforr_standard_communities(std),
        "ext_communities": ext,
        "large_communities": large,
        "prepend_detected": prepend,
    }


def _clean_peer_description(desc: str | None) -> str:
    """Normaliza texto vindo de `Peer's description` / Description no verbose."""
    if not desc:
        return ""
    s = str(desc).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s.strip().strip("\"'").strip()


def _parse_peer_verbose(text: str) -> dict[str, Any]:
    """Extrai descrição, ASN, tipo e route-policy do `display bgp peer verbose`."""
    asn = _first(re.compile(r"(?:Remote AS|Peer AS)\s*[：:]\s*(\d+)", re.I), text)
    desc = _first(re.compile(r"Peer'?s?\s+description\s*[：:]\s*(.+)$", re.I | re.M), text)
    if not desc:
        desc = _first(re.compile(r"Description\s*[：:]\s*(.+)$", re.I | re.M), text)
    rt_in = _first(re.compile(r"Route Policy\(Import\)\s*[：:]\s*(\S+)", re.I), text)
    rt_out = _first(re.compile(r"Route Policy\(Export\)\s*[：:]\s*(\S+)", re.I), text)
    peer_type = _first(re.compile(r"Peer Type\s*[：:]\s*(\S+)", re.I), text)
    vrf = _first(re.compile(r"VPN-Instance\s*(?:Name)?\s*[：:]\s*(\S+)", re.I | re.M), text)
    return {
        "remote_asn": int(asn) if asn else None,
        "description": desc,
        "peer_type": peer_type,
        "route_policy_import": rt_in,
        "route_policy_export": rt_out,
        "vrf_name": (vrf or "").strip(),
    }


# ── SSH helpers ─────────────────────────────────────────────────────────────

def _send(conn, cmd: str, log: list[str], timeout: int = 90) -> str:
    emit(log, f"SSH ← {cmd}")
    try:
        out = conn.send_command(cmd, read_timeout=timeout)
        return out or ""
    except Exception as e:
        emit(log, f"  ⚠ erro: {e}")
        return ""


def _is_valid_output(out: str) -> bool:
    if not out or len(out.strip()) < 20:
        return False
    low = out.lower()
    bad = ("unrecognized command", "error:", "^ error", "invalid")
    return not any(b in low[:120] for b in bad)


def _route_not_found(out: str) -> bool:
    low = out.lower()
    markers = (
        "does not exist",
        "no route",
        "not found",
        "no matching route",
        "info: the route does not exist",
        "info: no route found",
    )
    return any(m in low for m in markers)


# VRP varia entre versões (VRP5/8, NE, CX) na frase exacta antes da lista de IPs.
# Ordem: padrões mais específicos antes dos genéricos.
_ADV_TO_HEADER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # NE8000 / VRP8 típico
    re.compile(r"^\s*Advertised\s+to\s+such\s+\d+\s+peers?\s*:", re.I),
    re.compile(r"^\s*Advertised\s+to\s+total\s+\d+\s+peers?\s*:", re.I),
    # "Advertised to 14 peers" / "Advertised to 14 BGP peers" (sem "such")
    re.compile(r"^\s*Advertised\s+to\s+\d+\s+(?:BGP\s+)?peers?\s*:", re.I),
    re.compile(r"^\s*Advertised\s+to\s+(?:the\s+following|these)\s+peers?\s*:", re.I),
    # Cabeçalho sem contagem — só lista de IPs nas linhas seguintes
    re.compile(r"^\s*Advertised\s+to\s+peers?\s*:", re.I),
)


def _token_might_be_peer_ip(tok: str) -> str | None:
    """Normaliza token (vírgulas, parênteses Huawei) e devolve IP válido ou None."""
    t = (tok or "").strip().rstrip(",").strip()
    if len(t) >= 2 and t[0] == "(" and t[-1] == ")":
        t = t[1:-1].strip()
    if not t:
        return None
    try:
        ipaddress.ip_address(t)
    except ValueError:
        return None
    return t


def _parse_advertised_to_peers(detail_text: str) -> list[str]:
    """
    Extrai peers da secção Huawei (várias formulações entre versões VRP):

      Advertised to such 14 peers:
      Advertised to total 14 peers:
      Advertised to 14 peers:
      Advertised to 14 BGP peers:
      Advertised to the following peers:
      Advertised to peers:

    Não exige linha em branco após a lista. IPv4/IPv6, uma por linha ou na mesma linha após ':'.
    """
    if not detail_text:
        return []
    ips: list[str] = []
    lines = detail_text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not any(p.search(line) for p in _ADV_TO_HEADER_PATTERNS):
            i += 1
            continue
        # IPs opcionais na mesma linha do cabeçalho (ex.: "...peers: 10.0.0.1")
        if ":" in line:
            after = line.split(":", 1)[1].strip()
            for tok in after.split():
                ip = _token_might_be_peer_ip(tok)
                if ip is None:
                    break
                if ip not in ips:
                    ips.append(ip)
        i += 1
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                break
            ip = _token_might_be_peer_ip(stripped)
            if ip is None:
                break
            if ip not in ips:
                ips.append(ip)
            i += 1
    return ips


def _ssh_text_for_advertised_peers(detail_out: str, basic_out: str) -> str:
    """Junta passo2 + passo1: o detail muitas vezes não traz 'Advertised to…' (só o resumo)."""
    parts = [x for x in (detail_out or "", basic_out or "") if x and x.strip()]
    return "\n".join(parts) if parts else ""


def _peer_address_family(peer_ip: str) -> str:
    try:
        addr = ipaddress.ip_address(peer_ip)
        return "ipv6" if isinstance(addr, ipaddress.IPv6Address) else "ipv4"
    except ValueError:
        return "ipv4"


def _as_path_prepend_count(as_path: str | None) -> int:
    if not as_path:
        return 0
    nums = re.findall(r"\b(\d+)\b", as_path)
    if len(nums) < 2:
        return 0
    return sum(1 for a, b in zip(nums, nums[1:]) if a == b)


# ── Passos do fluxo K3G ─────────────────────────────────────────────────────

def _step1_basic(conn, prefix: str, plen: int, log: list[str]) -> tuple[str, list[str]]:
    """Passo 1 — confirmar existência na tabela BGP."""
    tried = []
    for cmd in (
        f"display bgp routing-table {prefix} {plen}",
        f"display bgp routing-table ipv4 {prefix} {plen}",
    ):
        tried.append(cmd)
        out = _send(conn, cmd, log)
        if _is_valid_output(out) and not _route_not_found(out):
            return out, tried
    return "", tried


def _step2_detail(conn, prefix: str, plen: int, log: list[str], *, explicit_cidr: bool) -> tuple[str, list[str]]:
    """Passo 2 — todos os paths com atributos completos."""
    tried = []
    cmd_list = [
        f"display bgp routing-table {prefix} {plen} detail",
        f"display bgp routing-table ipv4 {prefix} {plen} detail",
    ]
    # Só expande para busca ampla quando query NÃO foi explícita com máscara.
    if not explicit_cidr:
        cmd_list.append(f"display bgp routing-table {prefix}")

    for cmd in cmd_list:
        tried.append(cmd)
        out = _send(conn, cmd, log, timeout=120)
        if _is_valid_output(out) and not _route_not_found(out):
            return out, tried
    return "", tried


def _step3_peer_verbose(conn, peer_ip: str, log: list[str]) -> dict[str, Any]:
    """Passo 3 — info do peer (description, AS, route-policy) via verbose IPv4 ou IPv6."""
    if not peer_ip:
        return {}
    fam = _peer_address_family(peer_ip)
    if fam == "ipv6":
        cmds = (
            f"display bgp ipv6 peer {peer_ip} verbose",
            f"display bgp peer {peer_ip} verbose",
        )
    else:
        cmds = (
            f"display bgp peer {peer_ip} verbose",
            f"display bgp ipv6 peer {peer_ip} verbose",
        )
    out = ""
    for cmd in cmds:
        out = _send(conn, cmd, log, timeout=60)
        if _is_valid_output(out):
            break
    if not _is_valid_output(out):
        return {}
    info = _parse_peer_verbose(out)
    info["peer_ip"] = peer_ip
    info["raw"] = out[:3000]
    return info


def _step5_advertised_quick(conn, peer_ip: str, prefix: str, plen: int, log: list[str]) -> tuple[bool | None, str]:
    """Passo 5 — advertised-routes com grep do prefixo (rápido)."""
    needle = f"{prefix}/{plen}" if plen < 32 else prefix
    for cmd in (
        f"display bgp routing-table peer {peer_ip} advertised-routes | include {prefix}",
        f"display bgp routing-table peer {peer_ip} advertised-routes",
    ):
        out = _send(conn, cmd, log, timeout=60)
        if not _is_valid_output(out):
            continue
        if _route_not_found(out):
            return False, out[:500]
        if needle in out or (prefix in out and str(plen) in out):
            return True, out[:500]
        if len(out) > 100:
            return False, out[:500]
    return None, ""


def _step6_advertised_detail(
    conn, peer_ip: str, prefix: str, plen: int, log: list[str]
) -> dict[str, Any] | None:
    """Passo 6 — atributos do prefixo como enviado ao peer (post-policy)."""
    cmds = [
        f"display bgp routing-table peer {peer_ip} advertised-routes {prefix} {plen} detail",
        f"display bgp routing-table peer {peer_ip} advertised-routes ipv4 {prefix} {plen} detail",
    ]
    if _peer_address_family(peer_ip) == "ipv6":
        cmds.append(
            f"display bgp routing-table peer {peer_ip} advertised-routes ipv6 {prefix} {plen} detail"
        )
    for cmd in cmds:
        out = _send(conn, cmd, log, timeout=60)
        if _is_valid_output(out) and not _route_not_found(out):
            parsed = _parse_detail_block(out)
            parsed["prepend_count"] = _as_path_prepend_count(parsed.get("as_path"))
            parsed["raw"] = out[:3000]
            return parsed
    return None


def _step7_asn_regex(conn, asn: str, log: list[str]) -> tuple[str, list[str]]:
    """Passo 7 — busca por ASN com regular-expression."""
    tried = []
    chunks = []
    for cmd in (
        f"display bgp routing-table regular-expression ^{asn}$",
        f"display bgp routing-table regular-expression _{asn}_",
        f"display bgp routing-table regular-expression ^{asn}",
    ):
        tried.append(cmd)
        out = _send(conn, cmd, log, timeout=120)
        if _is_valid_output(out) and len(out.strip()) > 40:
            chunks.append(f"=== {cmd} ===\n{out}")
            if not chunks or len(chunks) < 2:
                continue
            break
        chunks.append(f"=== {cmd} ===\n{out}")
    return "\n\n".join(chunks), tried


# ── Ponto de entrada ────────────────────────────────────────────────────────

def run_huawei_bgp_export_lookup(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    vendor: str,
    query: str,
    local_asn: int | None,
    operator_peers: list[dict[str, Any]],
    log: list[str],
    peer_hints: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from netmiko import ConnectHandler

    primary_type = _vendor_netmiko(vendor)
    fallback_types = [primary_type]
    # Alguns ambientes Huawei aceitam melhor "huawei" que "huawei_vrp".
    if primary_type == "huawei_vrp":
        fallback_types.append("huawei")

    last_error: Exception | None = None
    for attempt in range(1, 4):
        for device_type in fallback_types:
            conn = None
            emit(log, f"SSH tentativa {attempt}/3 → {host}:{port} ({device_type})")
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
                    session_log=None,
                    global_delay_factor=1.2,
                )
                emit(log, "SSH autenticado com sucesso")
                return _investigate(
                    conn,
                    query=query,
                    local_asn=local_asn,
                    operator_peers=operator_peers,
                    peer_hints=peer_hints or {},
                    log=log,
                )
            except Exception as e:
                last_error = e
                emit(log, f"SSH falhou ({device_type}): {e!s}")
            finally:
                if conn:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
        if attempt < 3:
            time.sleep(1.2 * attempt)

    # Sem sucesso após retries/fallbacks
    assert last_error is not None
    raise last_error


def _investigate(
    conn,
    *,
    query: str,
    local_asn: int | None,
    operator_peers: list[dict[str, Any]],
    peer_hints: dict[str, dict[str, Any]],
    log: list[str],
) -> dict[str, Any]:
    all_cmds: list[str] = []
    raw_blocks: list[str] = []

    ip_mask = _try_ipv4(query)
    asn = _try_asn(query) if not ip_mask else None

    # ── Fluxo IP/prefixo ────────────────────────────────────────────────────
    if ip_mask:
        prefix, plen, explicit_cidr = ip_mask

        # Passo 1 — existência
        basic_out, c1 = _step1_basic(conn, prefix, plen, log)
        all_cmds += c1
        if basic_out:
            raw_blocks.append(f"=== passo1 ===\n{basic_out}")
        route_found = bool(basic_out) and not _route_not_found(basic_out)

        # Passo 2 — detail (paths completos)
        detail_out, c2 = _step2_detail(conn, prefix, plen, log, explicit_cidr=explicit_cidr)
        all_cmds += c2
        if detail_out:
            raw_blocks.append(f"=== passo2 detail ===\n{detail_out}")
            route_found = route_found or (not _route_not_found(detail_out))

        # Parsear paths do detail; fallback para passo1 quando detail não vier
        parse_src = detail_out or basic_out
        best_attrs = _parse_detail_block(parse_src) if parse_src else {}

        # Prepend no best path + detecção extra com local_asn
        prepend = best_attrs.get("prepend_detected", False)
        if local_asn and best_attrs.get("as_path_numbers"):
            la = str(local_asn)
            if sum(1 for n in best_attrs["as_path_numbers"] if n == la) >= 2:
                prepend = True

        # Passo 3 — peer origem: preferir dados do banco (SNMP); SSH verbose só se não houver hint.
        from_peer_ip = best_attrs.get("from_peer_ip")
        from_peer: dict[str, Any] = {}
        if from_peer_ip:
            hint = peer_hints.get(from_peer_ip) if peer_hints else None
            if hint and (hint.get("description") or hint.get("display_name")):
                disp = (hint.get("description") or hint.get("display_name") or "").strip()
                from_peer = {
                    "remote_asn": hint.get("remote_asn"),
                    "description": disp if disp and disp != from_peer_ip else None,
                    "peer_type": None,
                    "route_policy_import": None,
                    "route_policy_export": None,
                    "peer_ip": from_peer_ip,
                    "vrf_name": (hint.get("vrf_name") or "").strip(),
                    "raw": "",
                }
            else:
                from_peer = _step3_peer_verbose(conn, from_peer_ip, log)

        # Passos 5 + 6 — "Advertised to such …" costuma estar só no passo1; o detail pode omitir.
        advertised_peer_ips = _parse_advertised_to_peers(_ssh_text_for_advertised_peers(detail_out, basic_out))
        ops_by_ip = _operator_peers_by_ip(operator_peers)
        candidate_ips = list(advertised_peer_ips)
        advertised_to: list[dict[str, Any]] = []
        # Lista "Advertised to such … peers" + classificação / nome do banco (SNMP).
        for pip in candidate_ips[:50]:
            op = _pick_operator_peer(ops_by_ip, pip)
            hint = peer_hints.get(pip) if peer_hints else None
            display_name = (op.get("peer_name") or "").strip()
            if not display_name or display_name == pip:
                display_name = (hint.get("display_name") if hint else None) or pip
            remote_asn = op.get("remote_asn")
            if remote_asn is None and hint is not None:
                remote_asn = hint.get("remote_asn")
            peer_entry: dict[str, Any] = {
                "peer_ip": pip,
                "vrf_name": (op.get("vrf_name") or "").strip(),
                "peer_name": display_name,
                "role": op.get("role") or "unknown",
                "remote_asn": remote_asn,
                "advertises": True,
                "excerpt": "",
            }
            # AS-Path visto no advertised-routes (detail) para qualquer papel (Operadora/IX/CDN/Cliente/…).
            adv_detail = _step6_advertised_detail(conn, pip, prefix, plen, log)
            if adv_detail:
                adv_path = adv_detail.get("as_path")
                if adv_path and adv_detail.get("origin"):
                    adv_path = f"{adv_path}{str(adv_detail.get('origin'))[0]}"
                peer_entry["advertised_as_path"] = adv_path
                peer_entry["advertised_prepend_count"] = int(adv_detail.get("prepend_count") or 0)
            elif best_attrs.get("as_path"):
                fallback_path = best_attrs.get("as_path")
                if fallback_path and best_attrs.get("origin"):
                    fallback_path = f"{fallback_path}{str(best_attrs.get('origin'))[0]}"
                peer_entry["advertised_as_path"] = fallback_path
                peer_entry["advertised_prepend_count"] = _as_path_prepend_count(best_attrs.get("as_path"))
            advertised_to.append(peer_entry)

        return {
            "query": query.strip(),
            "route_found": route_found,
            "prepend_detected": prepend,
            "local_asn": local_asn,
            "as_path": best_attrs.get("as_path"),
            "origin": best_attrs.get("origin"),
            "local_pref": best_attrs.get("local_pref"),
            "med": best_attrs.get("med"),
            "nexthop": best_attrs.get("nexthop"),
            "from_peer_ip": from_peer_ip,
            "communities": best_attrs.get("communities", []),
            "ext_communities": best_attrs.get("ext_communities", []),
            "large_communities": best_attrs.get("large_communities", []),
            "from_peer": from_peer,
            "advertised_peer_ips": advertised_peer_ips,
            "advertised_to": advertised_to,
            "commands_tried": all_cmds,
            "raw_output": "\n\n".join(raw_blocks)[:14000],
            "log": log,
        }

    # ── Fluxo ASN ───────────────────────────────────────────────────────────
    if asn:
        raw, c7 = _step7_asn_regex(conn, asn, log)
        all_cmds += c7
        # Para cada rota encontrada, tenta pegar o detail do primeiro prefixo
        prefixes_found = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b", raw)
        best_attrs: dict[str, Any] = {}
        if prefixes_found:
            first_pfx = prefixes_found[0]
            parsed = _try_ipv4(first_pfx)
            p, pl = (parsed[0], parsed[1]) if parsed else (None, None)
            if p and pl is not None:
                detail_out, c2 = _step2_detail(conn, p, pl, log, explicit_cidr=True)
                all_cmds += c2
                if detail_out:
                    best_attrs = _parse_detail_block(detail_out)
                    raw += f"\n\n=== detail primeiro prefixo ===\n{detail_out}"

        route_found = bool(raw.strip()) and len(prefixes_found) > 0

        return {
            "query": query.strip(),
            "route_found": route_found,
            "prepend_detected": best_attrs.get("prepend_detected", False),
            "local_asn": local_asn,
            "as_path": best_attrs.get("as_path"),
            "origin": best_attrs.get("origin"),
            "local_pref": best_attrs.get("local_pref"),
            "med": best_attrs.get("med"),
            "nexthop": best_attrs.get("nexthop"),
            "from_peer_ip": best_attrs.get("from_peer_ip"),
            "communities": best_attrs.get("communities", []),
            "ext_communities": best_attrs.get("ext_communities", []),
            "large_communities": best_attrs.get("large_communities", []),
            "from_peer": {},
            "prefixes_found": prefixes_found[:50],
            "advertised_to": [],
            "commands_tried": all_cmds,
            "raw_output": raw[:14000],
            "log": log,
        }

    emit(log, f"Consulta não reconhecida como IPv4, CIDR ou ASN: {query!r}")
    return {
        "query": query.strip(),
        "route_found": False,
        "prepend_detected": False,
        "local_asn": local_asn,
        "as_path": None,
        "origin": None,
        "local_pref": None,
        "med": None,
        "nexthop": None,
        "from_peer_ip": None,
        "communities": [],
        "ext_communities": [],
        "large_communities": [],
        "from_peer": {},
        "advertised_to": [],
        "commands_tried": all_cmds,
        "raw_output": "",
        "log": log,
    }
