# Adaptado de netops_netbox_sync/app/parsers/bgp.py

from __future__ import annotations

import re


def parse_bgp_peers(output: str) -> list[dict]:
    peers: list[dict] = []
    local_as = None
    m_local = re.search(r"Local AS number\s*:\s*(\d+)", output)
    if m_local:
        local_as = int(m_local.group(1))
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+\d+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)",
            line,
        )
        if m:
            peers.append({
                "peer_ip": m.group(1),
                "peer_as": int(m.group(2)),
                "local_as": local_as,
                "state": m.group(3),
                "vrf_name": "",
            })
    return peers


def parse_bgp_peers_verbose(output: str, *, vrf_name: str = "") -> list[dict]:
    peers: list[dict] = []
    current: dict | None = None
    local_as = None
    router_id = None
    m_local = re.search(r"Local AS number\s*:\s*(\d+)", output)
    if m_local:
        local_as = int(m_local.group(1))
    m_rid = re.search(r"BGP local router ID\s*:\s*(\S+)", output)
    if m_rid:
        router_id = m_rid.group(1)
    vrf_norm = (vrf_name or "").strip()[:128]
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m_peer = re.match(r"BGP Peer is (\S+),\s+remote AS (\d+)", stripped)
        if m_peer:
            if current:
                peers.append(current)
            current = {
                "peer_ip": m_peer.group(1),
                "peer_as": int(m_peer.group(2)),
                "local_as": local_as,
                "router_id": router_id,
                "peer_type": None,
                "description": None,
                "state": None,
                "received_total_routes": None,
                "advertised_total_routes": None,
                "route_policy_import": None,
                "route_policy_export": None,
                "vrf_name": vrf_norm,
            }
            continue
        if current is None:
            continue
        m_type = re.match(r"Type:\s+(EBGP|IBGP) link", stripped)
        if m_type:
            current["peer_type"] = m_type.group(1)
            continue
        m_desc = re.match(r'Peer\'s description:\s+"(.+)"', stripped)
        if m_desc:
            current["description"] = m_desc.group(1)
            continue
        m_state = re.match(r"BGP current state:\s+(\w+)", stripped)
        if m_state:
            current["state"] = m_state.group(1)
            continue
        m_recv_routes = re.match(r"Received total routes:\s+(\d+)", stripped)
        if m_recv_routes:
            current["received_total_routes"] = int(m_recv_routes.group(1))
            continue
        m_adv_routes = re.match(r"Advertised total routes:\s+(\d+)", stripped)
        if m_adv_routes:
            current["advertised_total_routes"] = int(m_adv_routes.group(1))
            continue
        m_rt_in = re.match(
            r"Route Policy\s*\(\s*Import\s*\)\s*[：:]\s*(\S+)",
            stripped,
            re.IGNORECASE,
        )
        if m_rt_in:
            current["route_policy_import"] = m_rt_in.group(1).strip()
            continue
        m_rt_out = re.match(
            r"Route Policy\s*\(\s*Export\s*\)\s*[：:]\s*(\S+)",
            stripped,
            re.IGNORECASE,
        )
        if m_rt_out:
            current["route_policy_export"] = m_rt_out.group(1).strip()
            continue
    if current:
        peers.append(current)
    return peers
