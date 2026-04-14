"""
Atualização incremental via SNMP: admin/oper das interfaces e estado dos peers BGP,
sem recriar tabelas nem histórico de inventário (uso periódico, ex. a cada 2 min).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..models import BGPPeer, Device, Interface
from ..snmp_collector import async_collect_status_refresh
from .inventory_persist import is_ibgp_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def persist_snmp_status_refresh(
    db: AsyncSession,
    device_id: int,
    device: Device,
    log: list[str],
) -> dict:
    emit(log, "SNMP refresh de status (IF admin/oper + BGP FSM/counters)...")
    data = await async_collect_status_refresh(device.ip_address, device.snmp_community)

    if_result = await db.execute(select(Interface).where(Interface.device_id == device_id))
    by_name = {i.name: i for i in if_result.scalars().all()}
    updated_if = 0
    for iface in data["interfaces"]:
        name = iface.get("name")
        if not name or name not in by_name:
            continue
        row = by_name[name]
        admin = iface.get("admin_status")
        oper = iface.get("oper_status", "unknown")
        if row.admin_status != admin or row.status != oper:
            row.admin_status = admin
            row.status = oper
            row.last_updated = _now()
            updated_if += 1
        if not row.is_active:
            row.is_active = True
            row.deactivated_at = None
            row.last_updated = _now()

    local_as = data["bgp"].get("local_as")
    if local_as is not None:
        device.local_asn = int(local_as)

    peers_snmp = {p["peer_ip"]: p for p in data["bgp"]["peers"]}
    peer_result = await db.execute(select(BGPPeer).where(BGPPeer.device_id == device_id))
    updated_peer = 0
    for p in peer_result.scalars().all():
        # BGP4-MIB reflete só a instância principal; não sobrescrever peers de VRF com o mesmo IP.
        if (getattr(p, "vrf_name", None) or "").strip():
            continue
        sp = peers_snmp.get(p.peer_ip)
        if not sp:
            continue
        p.inventory_confirmed = True
        ra = sp.get("remote_as")
        ibgp = is_ibgp_session(local_as, ra)
        new_status = sp.get("state", "unknown")
        new_local = sp.get("local_addr")
        new_in = sp.get("in_updates")
        new_out = sp.get("out_updates")
        new_up = sp.get("uptime_secs")

        changed = (
            p.status != new_status
            or p.local_addr != new_local
            or p.remote_asn != ra
            or p.in_updates != new_in
            or p.out_updates != new_out
            or p.uptime_secs != new_up
            or p.is_ibgp != ibgp
        )
        if changed:
            p.status = new_status
            p.local_addr = new_local
            p.remote_asn = ra
            p.in_updates = new_in
            p.out_updates = new_out
            p.uptime_secs = new_up
            p.is_ibgp = ibgp
            p.last_updated = _now()
            updated_peer += 1
        if not p.is_active:
            p.is_active = True
            p.deactivated_at = None
            p.last_updated = _now()

    emit(
        log,
        f"Status SNMP gravado: {updated_if} interface(s) alterada(s), "
        f"{updated_peer} peer(s) BGP alterado(s)",
    )
    return {
        "updated_interface_rows": updated_if,
        "updated_peer_rows": updated_peer,
        "interface_names_seen": len(data["interfaces"]),
        "bgp_peers_seen": len(data["bgp"]["peers"]),
    }
