"""
Registra histórico de inserção, remoção e alteração do inventário coletado (SNMP).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..models import BGPPeer, Device, DeviceVrf, Interface, InventoryHistory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _j(obj: Any | None) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def _iface_row_db(i: Interface) -> dict[str, Any]:
    return {
        "name": i.name,
        "description": i.description,
        "ip_address": i.ip_address,
        "netmask": i.netmask,
        "ipv6_addresses": [x.strip() for x in (i.ipv6_addresses or "").split(",") if x.strip()],
        "admin_status": i.admin_status,
        "status": i.status,
        "speed_mbps": i.speed_mbps,
    }


def _iface_row_snmp(iface: dict) -> dict[str, Any]:
    return {
        "name": iface["name"],
        "description": iface.get("alias") or None,
        "ip_address": iface.get("ip_address"),
        "netmask": iface.get("netmask"),
        "ipv6_addresses": iface.get("ipv6_addresses") or [],
        "admin_status": iface.get("admin_status"),
        "status": iface.get("oper_status", "unknown"),
        "speed_mbps": iface.get("speed_mbps"),
    }


def _peer_entity_key_db(p: BGPPeer) -> str:
    vrf = (getattr(p, "vrf_name", None) or "").strip()
    return f"{p.peer_ip}|{vrf}" if vrf else p.peer_ip


def _peer_entity_key_snmp(peer: dict) -> str:
    ip = str(peer.get("peer_ip") or "")
    vrf = (peer.get("vrf_name") or "").strip()
    return f"{ip}|{vrf}" if vrf else ip


def _peer_stable_db(p: BGPPeer) -> dict[str, Any]:
    return {
        "peer_ip": p.peer_ip,
        "vrf_name": (getattr(p, "vrf_name", None) or "").strip(),
        "remote_asn": p.remote_asn,
        "local_addr": p.local_addr,
        "status": p.status,
        "is_ibgp": p.is_ibgp,
        "is_customer": p.is_customer,
        "is_provider": p.is_provider,
        "is_ix": p.is_ix,
        "is_cdn": p.is_cdn,
    }


def _peer_stable_snmp(peer: dict, is_ibgp: bool) -> dict[str, Any]:
    return {
        "peer_ip": peer["peer_ip"],
        "vrf_name": (peer.get("vrf_name") or "").strip(),
        "remote_asn": peer.get("remote_as"),
        "local_addr": peer.get("local_addr"),
        "status": peer.get("state", "unknown"),
        "is_ibgp": is_ibgp,
        # SNMP não altera classificação manual; comparamos com o que está no DB ao detectar update
    }


async def build_snmp_collect_history(
    db: AsyncSession,
    device_id: int,
    device: Device,
    data: dict[str, Any],
    *,
    source: str,
    is_ibgp_fn,
) -> tuple[str, list[InventoryHistory]]:
    """
    Compara estado atual do banco com o resultado SNMP e gera linhas de histórico (ainda não persistidas).
    """
    batch_id = str(uuid.uuid4())
    ts = _now()
    rows: list[InventoryHistory] = []

    def add_row(
        entity_type: str,
        action: str,
        entity_key: str,
        old: Any,
        new: Any,
    ) -> None:
        rows.append(
            InventoryHistory(
                device_id=device_id,
                created_at=ts,
                source=source,
                entity_type=entity_type,
                action=action,
                entity_key=entity_key[:255],
                old_json=_j(old),
                new_json=_j(new),
                batch_id=batch_id,
            )
        )

    # — Interfaces —
    r_if = await db.execute(select(Interface).where(Interface.device_id == device_id))
    old_if = {i.name: _iface_row_db(i) for i in r_if.scalars().all() if i.is_active}
    new_if = {iface["name"]: _iface_row_snmp(iface) for iface in data["interfaces"]}
    all_names = set(old_if) | set(new_if)
    for name in sorted(all_names):
        o, n = old_if.get(name), new_if.get(name)
        if o is None and n is not None:
            add_row("interface", "insert", name, None, n)
        elif o is not None and n is None:
            add_row("interface", "delete", name, o, None)
        elif o is not None and n is not None and o != n:
            add_row("interface", "update", name, o, n)

    # — BGP peers (campos estáveis + classificação manual do DB) —
    local_as = data["bgp"].get("local_as")
    r_pe = await db.execute(select(BGPPeer).where(BGPPeer.device_id == device_id))
    existing_peers = {
        _peer_entity_key_db(p): p for p in r_pe.scalars().all() if p.is_active
    }
    old_pe: dict[str, dict[str, Any]] = {
        k: _peer_stable_db(p) for k, p in existing_peers.items()
    }

    new_pe: dict[str, dict[str, Any]] = {}
    for peer in data["bgp"]["peers"]:
        k = _peer_entity_key_snmp(peer)
        ibgp = bool(is_ibgp_fn(local_as, peer.get("remote_as")))
        snap = _peer_stable_snmp(peer, ibgp)
        if k in existing_peers:
            ep = existing_peers[k]
            snap["is_customer"] = ep.is_customer
            snap["is_provider"] = ep.is_provider
            snap["is_ix"] = ep.is_ix
            snap["is_cdn"] = ep.is_cdn
        else:
            snap["is_customer"] = True
            snap["is_provider"] = False
            snap["is_ix"] = False
            snap["is_cdn"] = False
        new_pe[k] = snap

    all_keys = set(old_pe) | set(new_pe)
    for k in sorted(all_keys):
        o, n = old_pe.get(k), new_pe.get(k)
        if o is None and n is not None:
            add_row("bgp_peer", "insert", k, None, n)
        elif o is not None and n is None:
            ep = existing_peers.get(k)
            if ep is not None and getattr(ep, "inventory_confirmed", False):
                add_row("bgp_peer", "delete", k, o, None)
        elif o is not None and n is not None and o != n:
            add_row("bgp_peer", "update", k, o, n)

    # — VRFs —
    r_v = await db.execute(select(DeviceVrf).where(DeviceVrf.device_id == device_id))
    old_v = {v.vrf_name for v in r_v.scalars().all()}
    new_v = set(data.get("vrfs") or [])
    for name in sorted(new_v - old_v):
        add_row("vrf", "insert", name, None, {"vrf_name": name})
    for name in sorted(old_v - new_v):
        add_row("vrf", "delete", name, {"vrf_name": name}, None)

    # — AS local (device) —
    new_la = data["bgp"].get("local_as")
    if new_la is not None:
        new_la = int(new_la)
    old_la = device.local_asn
    if old_la != new_la:
        add_row(
            "device_asn",
            "update",
            "local_asn",
            {"local_asn": old_la},
            {"local_asn": new_la},
        )

    return batch_id, rows


async def persist_history_rows(db: AsyncSession, rows: list[InventoryHistory], log: list[str]) -> None:
    for row in rows:
        db.add(row)
    await db.flush()
    if rows:
        emit(log, f"Histórico: {len(rows)} evento(s) registrado(s) (lote {rows[0].batch_id[:8]}…).")


async def sync_device_vrfs_table(db: AsyncSession, device_id: int, vrf_names: list[str]) -> None:
    await db.execute(delete(DeviceVrf).where(DeviceVrf.device_id == device_id))
    ts = _now()
    for name in sorted(set(vrf_names)):
        if name:
            db.add(DeviceVrf(device_id=device_id, vrf_name=name[:128], last_seen_at=ts))
    await db.flush()


async def record_bgp_peer_role_change(
    db: AsyncSession,
    device_id: int,
    peer_ip: str,
    old_customer: bool,
    old_provider: bool,
    old_ix: bool,
    old_cdn: bool,
    new_customer: bool,
    new_provider: bool,
    new_ix: bool,
    new_cdn: bool,
    *,
    vrf_name: str = "",
    source: str = "api",
) -> None:
    batch_id = str(uuid.uuid4())
    vrf = (vrf_name or "").strip()
    raw_key = f"{peer_ip}|{vrf}" if vrf else peer_ip
    key = raw_key[:255] if len(raw_key) > 255 else raw_key
    db.add(
        InventoryHistory(
            device_id=device_id,
            created_at=_now(),
            source=source,
            entity_type="bgp_peer",
            action="update",
            entity_key=key,
            old_json=_j(
                {
                    "is_customer": old_customer,
                    "is_provider": old_provider,
                    "is_ix": old_ix,
                    "is_cdn": old_cdn,
                }
            ),
            new_json=_j(
                {
                    "is_customer": new_customer,
                    "is_provider": new_provider,
                    "is_ix": new_ix,
                    "is_cdn": new_cdn,
                }
            ),
            batch_id=batch_id,
        )
    )
    await db.flush()
