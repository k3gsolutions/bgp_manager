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


def _is_ip_token(token: str) -> bool:
    try:
        ipaddress.ip_address((token or "").strip())
        return True
    except ValueError:
        return False


def _parse_peer_policies_from_running_config(running_config: str) -> dict[tuple[str, str], dict[str, str]]:
    """
    Extrai route-policy de peer direto da configuração salva (backup).

    Retorna um mapa:
      (peer_ip, vrf_name) -> {"route_policy_import": "...", "route_policy_export": "..."}

    Observação: para peers da instância global, ``vrf_name`` é string vazia.
    """
    out: dict[tuple[str, str], dict[str, str]] = {}
    # peer-group -> políticas por contexto de VRF
    group_policies: dict[tuple[str, str], dict[str, str]] = {}
    # peer IP -> peer-group por contexto de VRF
    peer_group_ref: dict[tuple[str, str], str] = {}
    vrf_ctx = ""
    # Ex.: peer 2001:db8::1 route-policy C02-IMPORT-IPV6 import
    rx_peer_pol = re.compile(r"^\s*peer\s+(\S+)\s+route-policy\s+(\S+)\s+(import|export)\b", re.I)
    rx_peer_group = re.compile(r"^\s*peer\s+(\S+)\s+group\s+(\S+)\b", re.I)
    rx_vrf = re.compile(r"^\s*ipv(?:4|6)-family\s+vpn-instance\s+(\S+)\s*$", re.I)
    rx_global_fam = re.compile(r"^\s*ipv(?:4|6)-family\s+\S+", re.I)

    for raw in (running_config or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "#":
            vrf_ctx = ""
            continue
        m_vrf = rx_vrf.match(line)
        if m_vrf:
            vrf_ctx = (m_vrf.group(1) or "").strip()[:128]
            continue
        if rx_global_fam.match(line):
            vrf_ctx = ""
            continue
        m_grp = rx_peer_group.match(line)
        if m_grp:
            lhs = (m_grp.group(1) or "").strip()
            grp = (m_grp.group(2) or "").strip()
            if _is_ip_token(lhs) and grp:
                peer_group_ref[(lhs, vrf_ctx)] = grp

        m = rx_peer_pol.match(line)
        if not m:
            continue
        peer_ip = (m.group(1) or "").strip()
        policy = (m.group(2) or "").strip()
        direction = (m.group(3) or "").strip().lower()
        if _is_ip_token(peer_ip):
            key = (peer_ip, vrf_ctx)
            row = out.setdefault(key, {})
        else:
            gkey = (peer_ip, vrf_ctx)
            row = group_policies.setdefault(gkey, {})
        if direction == "import":
            row["route_policy_import"] = policy
        elif direction == "export":
            row["route_policy_export"] = policy

    # Herdança de policies por peer-group para peers sem policy direta
    for key, grp in peer_group_ref.items():
        p = out.setdefault(key, {})
        if p.get("route_policy_import") and p.get("route_policy_export"):
            continue
        gp = group_policies.get((grp, key[1])) or group_policies.get((grp, ""))
        if not gp:
            continue
        if not p.get("route_policy_import") and gp.get("route_policy_import"):
            p["route_policy_import"] = gp["route_policy_import"]
        if not p.get("route_policy_export") and gp.get("route_policy_export"):
            p["route_policy_export"] = gp["route_policy_export"]
    return out


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
    policy_from_backup = _parse_peer_policies_from_running_config(raw.get("running_config", ""))
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
        # Prioridade: verbose do peer; fallback: backup running-config.
        p_import = (s.get("route_policy_import") or "").strip()[:512] or None
        p_export = (s.get("route_policy_export") or "").strip()[:512] or None
        if not p_import or not p_export:
            by_cfg = policy_from_backup.get(dedupe_key) or policy_from_backup.get((pip, ""))
            if by_cfg:
                if not p_import:
                    p_import = (by_cfg.get("route_policy_import") or "").strip()[:512] or None
                if not p_export:
                    p_export = (by_cfg.get("route_policy_export") or "").strip()[:512] or None
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
            "route_policy_import": p_import,
            "route_policy_export": p_export,
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
