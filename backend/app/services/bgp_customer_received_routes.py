"""
Lista de prefixos BGP **recebidos** de um peer **Cliente** via SSH (Huawei VRP).
Comando base ``received-routes`` (mesmo layout de tabela que ``advertised-routes``).
"""

from __future__ import annotations

from typing import Any

from ..activity_log import emit
from .bgp_export_lookup import (
    _is_valid_output,
    _peer_address_family,
    _route_not_found,
    _send,
)
from .bgp_provider_advertised_routes import (
    MAX_DISPLAY_ROUTES,
    PAGE_SIZE,
    _parse_advertised_routes_table,
    _parse_reported_total,
)


def _received_list_cmds(peer_ip: str, vrf_name: str) -> list[str]:
    """
    VRP: NLRI recebidos do peer (mesma ordem de sintaxe que advertised-routes).

    - VRF IPv4: ``display bgp vpnv4 vpn-instance <VRF> routing-table peer <ip> received-routes``
    - VRF IPv6: ``display bgp vpnv6 vpn-instance <VRF> routing-table peer <ip> received-routes``
    """
    vrf = (vrf_name or "").strip()
    fam = _peer_address_family(peer_ip)
    if not vrf:
        if fam == "ipv6":
            return [
                f"display bgp ipv6 routing-table peer {peer_ip} received-routes",
                f"display bgp routing-table peer {peer_ip} received-routes",
            ]
        return [f"display bgp routing-table peer {peer_ip} received-routes"]
    if fam == "ipv6":
        return [
            f"display bgp vpnv6 vpn-instance {vrf} routing-table peer {peer_ip} received-routes",
            f"display bgp ipv6 routing-table vpn-instance {vrf} peer {peer_ip} received-routes",
            f"display bgp routing-table vpn-instance {vrf} peer {peer_ip} received-routes",
        ]
    return [
        f"display bgp vpnv4 vpn-instance {vrf} routing-table peer {peer_ip} received-routes",
        f"display bgp routing-table vpn-instance {vrf} peer {peer_ip} received-routes",
    ]


def run_huawei_customer_peer_received_routes(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    vendor: str,
    peer_ip: str,
    vrf_name: str,
    offset: int,
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
            emit(log, f"SSH OK ({device_type}) — received-routes para peer {peer_ip}")
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
        for cmd in _received_list_cmds(peer_ip, vrf_name):
            raw_list = _send(conn, cmd, log, timeout=120)
            if _is_valid_output(raw_list) and not _route_not_found(raw_list):
                break
        if not _is_valid_output(raw_list) or _route_not_found(raw_list):
            return {
                "error": "empty",
                "message": "Sem saída válida de received-routes (sessão ou tabela vazia).",
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
                f"Aviso: {full_total} rotas na tabela received-routes; listagem limitada a {MAX_DISPLAY_ROUTES} nesta UI.",
            )
            rows_for_ui = all_rows[:MAX_DISPLAY_ROUTES]
            cap_message = (
                f"Foram detetadas {full_total} rotas recebidas deste peer; "
                f"por performance só as primeiras {MAX_DISPLAY_ROUTES} são listadas aqui (paginação de {PAGE_SIZE} em {PAGE_SIZE}). "
                "Para o inventário completo use a CLI no equipamento."
            )
        else:
            rows_for_ui = all_rows
            cap_message = None

        n_display = len(rows_for_ui)
        slice_rows = rows_for_ui[offset : offset + PAGE_SIZE]
        has_more = offset + len(slice_rows) < n_display

        return {
            "too_many": False,
            "message": cap_message,
            "items": slice_rows,
            "total": n_display,
            "reported_total": reported_total,
            "offset": offset,
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
