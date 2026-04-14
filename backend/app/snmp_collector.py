"""
Coletor SNMP para dispositivos de rede (Huawei NE8000 e compatíveis).
Coleta: interfaces, IPs de interfaces, sessões BGP, VRFs.
Usa pysnmp v7 hlapi.v1arch.asyncio (fully async).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
from typing import Optional

from pysnmp.hlapi.v1arch.asyncio import (
    CommunityData,
    UdpTransportTarget,
    ObjectType,
    ObjectIdentity,
    SnmpDispatcher,
    get_cmd,
    walk_cmd,
)


# ── OIDs ───────────────────────────────────────────────────────────────────
class OID:
    # System
    SYS_DESCR   = "1.3.6.1.2.1.1.1.0"
    SYS_NAME    = "1.3.6.1.2.1.1.5.0"
    SYS_UPTIME  = "1.3.6.1.2.1.1.3.0"

    # Interface table (IF-MIB)
    IF_DESCR        = "1.3.6.1.2.1.2.2.1.2"
    IF_SPEED        = "1.3.6.1.2.1.2.2.1.5"
    IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
    IF_OPER_STATUS  = "1.3.6.1.2.1.2.2.1.8"
    IF_IN_OCTETS    = "1.3.6.1.2.1.2.2.1.10"
    IF_OUT_OCTETS   = "1.3.6.1.2.1.2.2.1.16"

    # IF-MIB extensions (ifXTable) — 64-bit counters + alias
    IF_ALIAS        = "1.3.6.1.2.1.31.1.1.1.18"
    IF_HC_IN        = "1.3.6.1.2.1.31.1.1.1.6"
    IF_HC_OUT       = "1.3.6.1.2.1.31.1.1.1.10"
    IF_HIGH_SPEED   = "1.3.6.1.2.1.31.1.1.1.15"  # Mbps

    # IP-MIB — IP addresses per interface
    IP_AD_ADDR      = "1.3.6.1.2.1.4.20.1.1"
    IP_AD_IF_INDEX  = "1.3.6.1.2.1.4.20.1.2"
    IP_AD_NET_MASK  = "1.3.6.1.2.1.4.20.1.3"
    IP_ADDR_IFINDEX = "1.3.6.1.2.1.4.34.1.3"
    IPV6_ADDR_PFXLEN = "1.3.6.1.2.1.55.1.8.1.2"

    # BGP4-MIB (RFC 1657)
    BGP_PEER_STATE      = "1.3.6.1.2.1.15.3.1.2"
    BGP_PEER_LOCAL_ADDR = "1.3.6.1.2.1.15.3.1.5"
    BGP_PEER_REMOTE_AS  = "1.3.6.1.2.1.15.3.1.9"
    BGP_PEER_IN_UPD     = "1.3.6.1.2.1.15.3.1.11"
    BGP_PEER_OUT_UPD    = "1.3.6.1.2.1.15.3.1.12"
    BGP_PEER_FSM_TIME   = "1.3.6.1.2.1.15.3.1.24"
    BGP_LOCAL_AS        = "1.3.6.1.2.1.15.2.0"

    # MPLS-VPN-MIB — VRF names
    MPLS_VPN_VRF_NAME = "1.3.6.1.2.1.10.166.11.1.2.2.1.1"


BGP_STATES = {
    "1": "idle",
    "2": "connect",
    "3": "active",
    "4": "opensent",
    "5": "openconfirm",
    "6": "established",
}

ADMIN_STATUS = {"1": "up", "2": "down", "3": "testing"}
OPER_STATUS  = {"1": "up", "2": "down", "3": "testing",
                "4": "unknown", "5": "dormant", "6": "notPresent", "7": "lowerLayerDown"}


# ── Data classes ────────────────────────────────────────────────────────────
@dataclass
class SNMPInterface:
    index: int
    name: str
    alias: str = ""
    admin_status: str = "unknown"
    oper_status: str = "unknown"
    speed_mbps: Optional[int] = None
    ip_address: Optional[str] = None
    netmask: Optional[str] = None
    ipv6_addresses: list[str] = field(default_factory=list)
    in_octets: Optional[int] = None
    out_octets: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "alias": self.alias,
            "admin_status": self.admin_status,
            "oper_status": self.oper_status,
            "speed_mbps": self.speed_mbps,
            "ip_address": self.ip_address,
            "netmask": self.netmask,
            "ipv6_addresses": self.ipv6_addresses,
            "in_octets": self.in_octets,
            "out_octets": self.out_octets,
        }


@dataclass
class SNMPBGPPeer:
    peer_ip: str
    remote_as: Optional[int] = None
    state: str = "unknown"
    local_addr: Optional[str] = None
    in_updates: Optional[int] = None
    out_updates: Optional[int] = None
    uptime_secs: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "peer_ip": self.peer_ip,
            "remote_as": self.remote_as,
            "state": self.state,
            "local_addr": self.local_addr,
            "in_updates": self.in_updates,
            "out_updates": self.out_updates,
            "uptime_secs": self.uptime_secs,
            "vrf_name": "",
        }


@dataclass
class SNMPResult:
    sys_name: str = ""
    sys_descr: str = ""
    uptime_secs: Optional[int] = None
    local_as: Optional[int] = None
    interfaces: list[SNMPInterface] = field(default_factory=list)
    bgp_peers: list[SNMPBGPPeer] = field(default_factory=list)
    vrfs: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ── Low-level SNMP helpers ──────────────────────────────────────────────────
async def _make_target(ip: str) -> UdpTransportTarget:
    return await UdpTransportTarget.create((ip, 161), timeout=3, retries=1)


async def _snmp_get(dispatcher: SnmpDispatcher, ip: str, community: str, oid: str) -> Optional[str]:
    target = await _make_target(ip)
    errInd, errStat, _, varBinds = await get_cmd(
        dispatcher,
        CommunityData(community, mpModel=1),
        target,
        ObjectType(ObjectIdentity(oid)),
    )
    if errInd or errStat:
        return None
    for vb in varBinds:
        return str(vb[1])
    return None


async def _snmp_walk(dispatcher: SnmpDispatcher, ip: str, community: str, oid: str) -> dict[str, str]:
    """Walk an OID subtree. Returns {full_oid_str: value_str}."""
    result: dict[str, str] = {}
    target = await _make_target(ip)
    async for errInd, errStat, _, varBinds in walk_cmd(
        dispatcher,
        CommunityData(community, mpModel=1),
        target,
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if errInd or errStat:
            break
        for vb in varBinds:
            result[str(vb[0])] = str(vb[1])
    return result


def _index_from_oid(oid_str: str, base: str) -> Optional[str]:
    """Extract the index suffix from a full OID string."""
    prefix = base + "."
    if oid_str.startswith(prefix):
        return oid_str[len(prefix):]
    return None


def _parse_ipv6_from_ipaddress_index(idx: str) -> str | None:
    """
    IP-MIB ipAddressIfIndex index: <addrType>.<addrLen>.<octets...>
    IPv6 = addrType 2 and addrLen 16.
    """
    try:
        parts = [int(x) for x in idx.split(".")]
    except ValueError:
        return None
    if len(parts) < 18:
        return None
    addr_type = parts[0]
    addr_len = parts[1]
    # RFC4001: ipv6=2 (16 bytes), ipv6z=4 (20 bytes -> 16 bytes addr + 4 scope zone)
    if addr_type == 2 and addr_len == 16:
        octets = parts[2:18]
    elif addr_type == 4 and addr_len == 20 and len(parts) >= 22:
        octets = parts[2:18]
    else:
        return None
    if len(octets) != 16:
        return None
    try:
        return str(ipaddress.IPv6Address(bytes(octets)))
    except ValueError:
        return None


def _parse_ipv6_from_ipv6mib_index(idx: str) -> tuple[str, str] | None:
    """
    IPV6-MIB ipv6AddrPfxLength index: <ifIndex>.<16-byte IPv6 address>
    """
    try:
        parts = [int(x) for x in idx.split(".")]
    except ValueError:
        return None
    if len(parts) < 17:
        return None
    ifidx = str(parts[0])
    octets = parts[1:17]
    if len(octets) != 16:
        return None
    try:
        addr = str(ipaddress.IPv6Address(bytes(octets)))
    except ValueError:
        return None
    return ifidx, addr


# ── Main collection functions (fully async) ─────────────────────────────────
async def collect_interfaces(dispatcher: SnmpDispatcher, ip: str, community: str) -> list[SNMPInterface]:
    descr_raw  = await _snmp_walk(dispatcher, ip, community, OID.IF_DESCR)
    alias_raw  = await _snmp_walk(dispatcher, ip, community, OID.IF_ALIAS)
    admin_raw  = await _snmp_walk(dispatcher, ip, community, OID.IF_ADMIN_STATUS)
    oper_raw   = await _snmp_walk(dispatcher, ip, community, OID.IF_OPER_STATUS)
    speed_raw  = await _snmp_walk(dispatcher, ip, community, OID.IF_HIGH_SPEED)
    hcin_raw   = await _snmp_walk(dispatcher, ip, community, OID.IF_HC_IN)
    hcout_raw  = await _snmp_walk(dispatcher, ip, community, OID.IF_HC_OUT)

    interfaces: dict[str, SNMPInterface] = {}
    for oid_str, val in descr_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_DESCR)
        if idx:
            interfaces[idx] = SNMPInterface(index=int(idx), name=val)

    for oid_str, val in alias_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_ALIAS)
        if idx and idx in interfaces:
            interfaces[idx].alias = val

    for oid_str, val in admin_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_ADMIN_STATUS)
        if idx and idx in interfaces:
            interfaces[idx].admin_status = ADMIN_STATUS.get(val, val)

    for oid_str, val in oper_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_OPER_STATUS)
        if idx and idx in interfaces:
            interfaces[idx].oper_status = OPER_STATUS.get(val, val)

    for oid_str, val in speed_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_HIGH_SPEED)
        if idx and idx in interfaces:
            try:
                interfaces[idx].speed_mbps = int(val)
            except ValueError:
                pass

    for oid_str, val in hcin_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_HC_IN)
        if idx and idx in interfaces:
            try:
                interfaces[idx].in_octets = int(val)
            except ValueError:
                pass

    for oid_str, val in hcout_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_HC_OUT)
        if idx and idx in interfaces:
            try:
                interfaces[idx].out_octets = int(val)
            except ValueError:
                pass

    # Map IP addresses to interfaces
    ip_ifidx_raw = await _snmp_walk(dispatcher, ip, community, OID.IP_AD_IF_INDEX)
    ip_mask_raw  = await _snmp_walk(dispatcher, ip, community, OID.IP_AD_NET_MASK)

    ip_to_ifidx: dict[str, str] = {}
    for oid_str, val in ip_ifidx_raw.items():
        ip_key = _index_from_oid(oid_str, OID.IP_AD_IF_INDEX)
        if ip_key:
            ip_to_ifidx[ip_key] = val  # val is the ifIndex

    ip_to_mask: dict[str, str] = {}
    for oid_str, val in ip_mask_raw.items():
        ip_key = _index_from_oid(oid_str, OID.IP_AD_NET_MASK)
        if ip_key:
            # Alguns agentes retornam máscara como OctetString binária (ex.: ÿÿÿü)
            # ao invés de dotted-decimal.
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", val or ""):
                ip_to_mask[ip_key] = val
            elif val and len(val) == 4:
                ip_to_mask[ip_key] = ".".join(str(ord(ch)) for ch in val)
            else:
                ip_to_mask[ip_key] = val

    for ip_addr_key, ifidx in ip_to_ifidx.items():
        if ifidx in interfaces:
            interfaces[ifidx].ip_address = ip_addr_key
            interfaces[ifidx].netmask = ip_to_mask.get(ip_addr_key)

    # IPv6 addresses by interface (best effort)
    ip_addr_ifindex_raw = await _snmp_walk(dispatcher, ip, community, OID.IP_ADDR_IFINDEX)
    ipv6_pfxlen_raw = await _snmp_walk(dispatcher, ip, community, OID.IPV6_ADDR_PFXLEN)

    ipv6_prefix_by_ifaddr: dict[tuple[str, str], str] = {}
    for oid_str, val in ipv6_pfxlen_raw.items():
        idx = _index_from_oid(oid_str, OID.IPV6_ADDR_PFXLEN)
        if not idx:
            continue
        parsed = _parse_ipv6_from_ipv6mib_index(idx)
        if not parsed:
            continue
        ifidx, addr = parsed
        ipv6_prefix_by_ifaddr[(ifidx, addr)] = val

    for oid_str, val in ip_addr_ifindex_raw.items():
        idx = _index_from_oid(oid_str, OID.IP_ADDR_IFINDEX)
        if not idx:
            continue
        addr = _parse_ipv6_from_ipaddress_index(idx)
        if not addr:
            continue
        ifidx = val
        if ifidx not in interfaces:
            continue
        pfx = ipv6_prefix_by_ifaddr.get((ifidx, addr))
        cidr = f"{addr}/{pfx}" if pfx and pfx.isdigit() else addr
        if cidr not in interfaces[ifidx].ipv6_addresses:
            interfaces[ifidx].ipv6_addresses.append(cidr)

    return sorted(interfaces.values(), key=lambda x: x.index)


async def collect_interface_status_only(
    dispatcher: SnmpDispatcher, ip: str, community: str
) -> list[dict[str, str]]:
    """Somente nome + admin/oper (sem IP, velocidade, contadores) — refresh rápido."""
    descr_raw = await _snmp_walk(dispatcher, ip, community, OID.IF_DESCR)
    admin_raw = await _snmp_walk(dispatcher, ip, community, OID.IF_ADMIN_STATUS)
    oper_raw = await _snmp_walk(dispatcher, ip, community, OID.IF_OPER_STATUS)

    by_idx: dict[str, dict[str, str]] = {}
    for oid_str, val in descr_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_DESCR)
        if idx:
            by_idx[idx] = {
                "name": val,
                "admin_status": "unknown",
                "oper_status": "unknown",
            }

    for oid_str, val in admin_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_ADMIN_STATUS)
        if idx and idx in by_idx:
            by_idx[idx]["admin_status"] = ADMIN_STATUS.get(val, val)

    for oid_str, val in oper_raw.items():
        idx = _index_from_oid(oid_str, OID.IF_OPER_STATUS)
        if idx and idx in by_idx:
            by_idx[idx]["oper_status"] = OPER_STATUS.get(val, val)

    return list(by_idx.values())


async def collect_bgp(dispatcher: SnmpDispatcher, ip: str, community: str) -> tuple[list[SNMPBGPPeer], Optional[int]]:
    state_raw     = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_STATE)
    local_raw     = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_LOCAL_ADDR)
    remote_as_raw = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_REMOTE_AS)
    in_upd_raw    = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_IN_UPD)
    out_upd_raw   = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_OUT_UPD)
    fsm_raw       = await _snmp_walk(dispatcher, ip, community, OID.BGP_PEER_FSM_TIME)

    local_as_str = await _snmp_get(dispatcher, ip, community, OID.BGP_LOCAL_AS)
    local_as = int(local_as_str) if local_as_str and local_as_str.isdigit() else None

    peers: dict[str, SNMPBGPPeer] = {}

    for oid_str, val in state_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_STATE)
        if peer_ip:
            peers[peer_ip] = SNMPBGPPeer(
                peer_ip=peer_ip,
                state=BGP_STATES.get(val, val),
            )

    for oid_str, val in local_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_LOCAL_ADDR)
        if peer_ip and peer_ip in peers:
            local = val
            if local and len(local) == 4 and "." not in local:
                local = ".".join(str(ord(ch)) for ch in local)
            peers[peer_ip].local_addr = local if local not in ("0.0.0.0", "") else None

    for oid_str, val in remote_as_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_REMOTE_AS)
        if peer_ip and peer_ip in peers:
            try:
                peers[peer_ip].remote_as = int(val)
            except ValueError:
                pass

    for oid_str, val in in_upd_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_IN_UPD)
        if peer_ip and peer_ip in peers:
            try:
                peers[peer_ip].in_updates = int(val)
            except ValueError:
                pass

    for oid_str, val in out_upd_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_OUT_UPD)
        if peer_ip and peer_ip in peers:
            try:
                peers[peer_ip].out_updates = int(val)
            except ValueError:
                pass

    for oid_str, val in fsm_raw.items():
        peer_ip = _index_from_oid(oid_str, OID.BGP_PEER_FSM_TIME)
        if peer_ip and peer_ip in peers:
            try:
                # timeticks (centiseconds) → seconds
                peers[peer_ip].uptime_secs = int(val) // 100
            except ValueError:
                pass

    return list(peers.values()), local_as


async def collect_vrfs(dispatcher: SnmpDispatcher, ip: str, community: str) -> list[str]:
    raw = await _snmp_walk(dispatcher, ip, community, OID.MPLS_VPN_VRF_NAME)
    vrfs = []
    for val in raw.values():
        v = val.strip()
        if v and v not in vrfs:
            vrfs.append(v)
    return sorted(vrfs)


async def collect_system(dispatcher: SnmpDispatcher, ip: str, community: str) -> tuple[str, str, Optional[int]]:
    sys_name   = await _snmp_get(dispatcher, ip, community, OID.SYS_NAME) or ""
    sys_descr  = await _snmp_get(dispatcher, ip, community, OID.SYS_DESCR) or ""
    uptime_raw = await _snmp_get(dispatcher, ip, community, OID.SYS_UPTIME)
    uptime_secs = int(uptime_raw) // 100 if uptime_raw and uptime_raw.isdigit() else None
    return sys_name, sys_descr, uptime_secs


# ── Top-level async entry point ─────────────────────────────────────────────
async def async_collect_status_refresh(ip: str, community: str) -> dict:
    """Uma sessão SNMP: status de interfaces + estado BGP (sem VRFs, sem system walk)."""
    dispatcher = SnmpDispatcher()
    try:
        ifaces = await collect_interface_status_only(dispatcher, ip, community)
        bgp_peers, local_as = await collect_bgp(dispatcher, ip, community)
    finally:
        dispatcher.close()

    return {
        "interfaces": ifaces,
        "bgp": {
            "local_as": local_as,
            "peers": [p.to_dict() for p in bgp_peers],
        },
    }


async def async_collect_all(ip: str, community: str) -> dict:
    """Coleta tudo de uma vez (sistema + interfaces + BGP + VRFs)."""
    dispatcher = SnmpDispatcher()
    try:
        sys_name, sys_descr, uptime = await collect_system(dispatcher, ip, community)
        interfaces = await collect_interfaces(dispatcher, ip, community)
        bgp_peers, local_as = await collect_bgp(dispatcher, ip, community)
        vrfs = await collect_vrfs(dispatcher, ip, community)
    finally:
        dispatcher.close()

    return {
        "sys_name": sys_name,
        "sys_descr": sys_descr,
        "uptime_secs": uptime,
        "local_as": local_as,
        "interfaces": [i.to_dict() for i in interfaces],
        "bgp": {
            "local_as": local_as,
            "peers": [p.to_dict() for p in bgp_peers],
        },
        "vrfs": vrfs,
    }


async def async_collect_interfaces(ip: str, community: str) -> list[dict]:
    dispatcher = SnmpDispatcher()
    try:
        result = await collect_interfaces(dispatcher, ip, community)
    finally:
        dispatcher.close()
    return [i.to_dict() for i in result]


async def async_collect_bgp(ip: str, community: str) -> dict:
    dispatcher = SnmpDispatcher()
    try:
        peers, local_as = await collect_bgp(dispatcher, ip, community)
    finally:
        dispatcher.close()
    return {
        "local_as": local_as,
        "peers": [p.to_dict() for p in peers],
    }
