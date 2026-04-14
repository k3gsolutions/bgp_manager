"""
Monta o payload interno de inventário a partir da saída CLI (collect_all + VRF BGP).
"""

from __future__ import annotations

import ipaddress
import re

from ..models import Device
from .parsers_bgp import parse_bgp_peers, parse_bgp_peers_verbose
from .parsers_if import (
    parse_interface_brief,
    parse_interface_description,
    parse_ip_interface_brief,
    parse_ipv6_interface_brief,
    parse_lag_members,
)
from .parsers_vrf import parse_vrfs


def _split_ip(addr: str) -> tuple[str | None, str | None]:
    if not addr or addr.lower() == "unassigned":
        return None, None
    try:
        if "/" in addr:
            iface = ipaddress.ip_interface(addr.strip())
            return str(iface.ip), str(iface.netmask)
        ipaddress.ip_address(addr.strip())
        return addr.strip(), None
    except ValueError:
        return addr.strip(), None


def _norm_state(state: str | None) -> str:
    if not state:
        return "unknown"
    return str(state).strip().lower()


def _parse_sys_from_version(version_out: str, device: Device) -> tuple[str, str]:
    name = (device.name or "").strip()
    m_host = re.search(r"\((\S+)\s+uptime\s+is", version_out, re.I)
    if m_host:
        name = m_host.group(1)
    lines = [l.strip() for l in version_out.splitlines() if l.strip()]
    descr = "\n".join(lines[:3]) if lines else ""
    return name or device.ip_address, descr[:2000]


def build_inventory_payload_from_cli(
    raw: dict[str, str],
    vrf_bgp: dict[str, str],
    device: Device,
) -> dict:
    summary_raw = raw.get("bgp_summary", "")
    m_las = re.search(r"Local AS number\s*:\s*(\d+)", summary_raw)
    local_as_global = int(m_las.group(1)) if m_las else None

    ifaces = parse_interface_brief(raw.get("interfaces_brief", ""))
    descriptions = parse_interface_description(raw.get("interfaces_desc", ""))
    ip_rows = parse_ip_interface_brief(raw.get("ip_interfaces", ""))
    ipv6_by_iface = parse_ipv6_interface_brief(raw.get("ipv6_interfaces", ""))
    lag_members = parse_lag_members(raw.get("running_config", ""))

    iface_names = {i["name"] for i in ifaces}
    for member_name, _parent in lag_members.items():
        if member_name not in iface_names:
            ifaces.append({
                "name": member_name,
                "admin_status": "up",
                "oper_status": "up",
            })
            iface_names.add(member_name)

    ip_by_iface: dict[str, tuple[str | None, str | None]] = {}
    for row in ip_rows:
        ip_by_iface[row["interface"]] = _split_ip(row["address"])

    snmp_like_ifaces: list[dict] = []
    for iface in ifaces:
        name = iface["name"]
        ip_addr, netmask = ip_by_iface.get(name, (None, None))
        snmp_like_ifaces.append({
            "name": name,
            "alias": descriptions.get(name),
            "ip_address": ip_addr,
            "netmask": netmask,
            "ipv6_addresses": ipv6_by_iface.get(name, []),
            "admin_status": iface.get("admin_status"),
            "oper_status": iface.get("oper_status", "unknown"),
            "speed_mbps": None,
            "in_octets": None,
            "out_octets": None,
        })

    if raw.get("bgp_peers_verbose", "").strip():
        ipv4_sessions = parse_bgp_peers_verbose(raw["bgp_peers_verbose"], vrf_name="")
    else:
        ipv4_sessions = parse_bgp_peers(raw.get("bgp_peers", ""))

    ipv6_sessions: list[dict] = []
    if raw.get("bgp_ipv6_verbose", "").strip():
        ipv6_sessions = parse_bgp_peers_verbose(raw["bgp_ipv6_verbose"], vrf_name="")

    vrf_sessions: list[dict] = []
    for key, output in (vrf_bgp or {}).items():
        vrf_nm = ""
        if isinstance(key, str) and ":" in key:
            vrf_nm = key.split(":", 1)[1].strip()
        vrf_sessions.extend(parse_bgp_peers_verbose(output, vrf_name=vrf_nm))

    ordered = ipv4_sessions + ipv6_sessions + vrf_sessions
    seen_peer: set[tuple[str, str]] = set()
    peers_out: list[dict] = []
    for s in ordered:
        pip = s.get("peer_ip")
        if not pip:
            continue
        vrf_r = (s.get("vrf_name") or "").strip()[:128]
        dedupe_key = (pip, vrf_r)
        if dedupe_key in seen_peer:
            continue
        seen_peer.add(dedupe_key)
        peer_as = s.get("peer_as")
        peers_out.append({
            "peer_ip": pip,
            "remote_as": peer_as,
            "local_addr": None,
            "state": _norm_state(s.get("state")),
            "vrf_name": vrf_r,
            # UI usa estes campos para contagem de rotas:
            # in_updates -> rotas recebidas
            # out_updates -> rotas anunciadas
            "in_updates": s.get("received_total_routes"),
            "out_updates": s.get("advertised_total_routes"),
            "uptime_secs": None,
        })

    vrfs = parse_vrfs(raw.get("vrfs", ""))
    vrf_names = [v["name"] for v in vrfs]

    sys_name, sys_descr = _parse_sys_from_version(raw.get("version", ""), device)

    return {
        "sys_name": sys_name,
        "sys_descr": sys_descr,
        "uptime_secs": None,
        "interfaces": snmp_like_ifaces,
        "bgp": {
            "local_as": local_as_global,
            "peers": peers_out,
        },
        "vrfs": vrf_names,
    }
