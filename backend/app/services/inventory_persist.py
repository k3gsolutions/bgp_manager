"""
Persistência unificada do inventário (interfaces, BGP, VRF) a partir de um payload
no formato interno — origem SNMP ou CLI Huawei.

Todo registro gravado em `Interface` / `BGPPeer` recebe o `device_id` informado;
não há compartilhamento de linhas entre equipamentos (cada device é uma entidade).
"""

from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..models import BGPPeer, Device, Interface, InterfaceMetric
from .interface_name import canonical_interface_name
from .inventory_history import (
    build_snmp_collect_history,
    persist_history_rows,
    sync_device_vrfs_table,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bgp_peer_row_key(peer: dict) -> tuple[str, str]:
    ip = str(peer["peer_ip"]).strip()
    vrf = (peer.get("vrf_name") or "").strip()[:128]
    return (ip, vrf)


def is_ibgp_session(local_as: int | None, remote_asn: int | None) -> bool:
    if local_as is None or remote_asn is None:
        return False
    return int(local_as) == int(remote_asn)


async def persist_inventory_payload(
    db: AsyncSession,
    device_id: int,
    device: Device,
    data: dict,
    log: list[str],
    *,
    source: str,
    collect_label: str = "SNMP",
) -> dict:
    """
    `data` no formato:
      sys_name, sys_descr, uptime_secs (opcional),
      interfaces: [{name, alias?, ip_address, netmask, admin_status, oper_status, speed_mbps?, in_octets?, out_octets?}],
      bgp: {local_as, peers: [{peer_ip, vrf_name?, remote_as, local_addr?, state, ...}]},
      vrfs: [str, ...]
    """
    emit(
        log,
        f"Sistema ({collect_label}): sysName={data.get('sys_name')!r} "
        f"uptime={data.get('uptime_secs')!r}",
    )

    _batch_id, hist_rows = await build_snmp_collect_history(
        db,
        device_id,
        device,
        data,
        source=source,
        is_ibgp_fn=is_ibgp_session,
    )
    await persist_history_rows(db, hist_rows, log)

    ssh_complement_only = str(source or "").startswith("ssh")

    prev_if = await db.execute(select(Interface).where(Interface.device_id == device_id))
    existing_if: dict[str, Interface] = {i.name: i for i in prev_if.scalars().all()}
    seen_if: set[str] = set()
    saved_interfaces: list[tuple[Interface, dict]] = []

    for iface in data["interfaces"]:
        name = canonical_interface_name(iface.get("name"))
        if not name:
            continue
        seen_if.add(name)
        if name in existing_if:
            db_iface = existing_if[name]
            if ssh_complement_only:
                # SNMP é fonte primária: em coleta SSH, apenas preenche lacunas.
                if not (db_iface.description or "").strip():
                    db_iface.description = iface.get("alias") or None
                if not db_iface.ip_address and iface.get("ip_address"):
                    db_iface.ip_address = iface.get("ip_address")
                if not db_iface.netmask and iface.get("netmask"):
                    db_iface.netmask = iface.get("netmask")
                cur_v6 = [x.strip() for x in (db_iface.ipv6_addresses or "").split(",") if x.strip()]
                new_v6 = [x for x in (iface.get("ipv6_addresses") or []) if str(x).strip()]
                if new_v6:
                    merged = list(dict.fromkeys(cur_v6 + new_v6))
                    db_iface.ipv6_addresses = ",".join(merged) or None
                if not (db_iface.admin_status or "").strip() and iface.get("admin_status"):
                    db_iface.admin_status = iface.get("admin_status")
                if (db_iface.status or "").strip().lower() in ("", "unknown"):
                    db_iface.status = iface.get("oper_status", "unknown")
                if db_iface.speed_mbps is None and iface.get("speed_mbps") is not None:
                    db_iface.speed_mbps = iface.get("speed_mbps")
            else:
                db_iface.description = iface.get("alias") or None
                db_iface.ip_address = iface.get("ip_address")
                db_iface.netmask = iface.get("netmask")
                db_iface.ipv6_addresses = ",".join(iface.get("ipv6_addresses") or []) or None
                db_iface.admin_status = iface.get("admin_status")
                db_iface.status = iface.get("oper_status", "unknown")
                db_iface.speed_mbps = iface.get("speed_mbps")
            db_iface.last_updated = _now()
            db_iface.is_active = True
            db_iface.deactivated_at = None
        else:
            db_iface = Interface(
                device_id=device_id,
                name=name,
                description=iface.get("alias") or None,
                ip_address=iface.get("ip_address"),
                netmask=iface.get("netmask"),
                ipv6_addresses=",".join(iface.get("ipv6_addresses") or []) or None,
                admin_status=iface.get("admin_status"),
                status=iface.get("oper_status", "unknown"),
                speed_mbps=iface.get("speed_mbps"),
                is_active=True,
                deactivated_at=None,
                last_updated=_now(),
            )
            db.add(db_iface)
        saved_interfaces.append((db_iface, iface))

    # Limpa lixo histórico de nomes antigos no padrão "<iface>(40G)" etc.
    # Quando o mesmo nome-base já existe no inventário atual, removemos o antigo do banco.
    for old_name, old_iface in list(existing_if.items()):
        m = re.match(r"^(.+)\([^()]+\)$", old_name)
        if not m:
            continue
        base_name = m.group(1).strip()
        if base_name in seen_if:
            await db.delete(old_iface)
            existing_if.pop(old_name, None)

    # Não remover do banco: marca como inativo só em coleta base (SNMP).
    # Em coleta SSH complementar, ausência não significa remoção real.
    if not ssh_complement_only:
        for name, old_iface in existing_if.items():
            if name not in seen_if and old_iface.is_active:
                old_iface.is_active = False
                old_iface.deactivated_at = _now()

    await db.flush()

    for db_iface, iface in saved_interfaces:
        if iface.get("in_octets") is not None or iface.get("out_octets") is not None:
            db.add(
                InterfaceMetric(
                    interface_id=db_iface.id,
                    timestamp=_now(),
                    in_octets=iface.get("in_octets"),
                    out_octets=iface.get("out_octets"),
                )
            )

    local_as = data["bgp"].get("local_as")
    if local_as is not None:
        device.local_asn = int(local_as)

    prev = await db.execute(select(BGPPeer).where(BGPPeer.device_id == device_id))
    existing: dict[tuple[str, str], BGPPeer] = {}
    for p in prev.scalars().all():
        vrf = (getattr(p, "vrf_name", None) or "").strip()[:128]
        existing[(p.peer_ip, vrf)] = p
    seen: set[tuple[str, str]] = set()

    for peer in data["bgp"]["peers"]:
        ip, vrf = _bgp_peer_row_key(peer)
        seen.add((ip, vrf))
        remote_asn = peer.get("remote_as")
        ibgp = is_ibgp_session(local_as, remote_asn)
        rpi = peer.get("route_policy_import")
        rpe = peer.get("route_policy_export")
        if isinstance(rpi, str):
            rpi = rpi.strip()[:512] or None
        else:
            rpi = None
        if isinstance(rpe, str):
            rpe = rpe.strip()[:512] or None
        else:
            rpe = None
        if (ip, vrf) in existing:
            p = existing[(ip, vrf)]
            p.remote_asn = remote_asn
            p.local_addr = peer.get("local_addr")
            p.in_updates = peer.get("in_updates")
            p.out_updates = peer.get("out_updates")
            p.uptime_secs = peer.get("uptime_secs")
            p.status = peer.get("state", "unknown")
            p.is_ibgp = ibgp
            p.is_active = True
            p.deactivated_at = None
            p.last_updated = _now()
            p.inventory_confirmed = True
            if rpi is not None:
                p.route_policy_import = rpi
            if rpe is not None:
                p.route_policy_export = rpe
        else:
            db.add(
                BGPPeer(
                    device_id=device_id,
                    peer_ip=ip,
                    vrf_name=vrf,
                    remote_asn=remote_asn,
                    local_addr=peer.get("local_addr"),
                    in_updates=peer.get("in_updates"),
                    out_updates=peer.get("out_updates"),
                    uptime_secs=peer.get("uptime_secs"),
                    status=peer.get("state", "unknown"),
                    is_customer=True,
                    is_provider=False,
                    is_ix=False,
                    is_cdn=False,
                    is_ibgp=ibgp,
                    is_active=True,
                    deactivated_at=None,
                    inventory_confirmed=True,
                    last_updated=_now(),
                    route_policy_import=rpi,
                    route_policy_export=rpe,
                )
            )

    # Só marcar inativo se o peer já tinha sido visto em coleta anterior; senão permanece ativo.
    for key, p in list(existing.items()):
        if key not in seen and p.is_active and getattr(p, "inventory_confirmed", False):
            p.is_active = False
            p.deactivated_at = _now()

    await db.flush()

    vrf_list = data["vrfs"]
    await sync_device_vrfs_table(db, device_id, vrf_list)

    emit(
        log,
        f"Gravado no banco: {len(data['interfaces'])} interface(s), "
        f"{len(data['bgp']['peers'])} peer(s) BGP, AS local={data['bgp'].get('local_as')}, "
        f"{len(vrf_list)} VRF(s)",
    )
    emit(log, f"Coleta {collect_label} finalizada com sucesso.")

    ipv6_count = sum(len(iface.get("ipv6_addresses") or []) for iface in data.get("interfaces", []))
    return {
        "collected_at": _now().isoformat(),
        "sys_name": data.get("sys_name"),
        "sys_descr": data.get("sys_descr"),
        "uptime_secs": data.get("uptime_secs"),
        "local_as": data["bgp"]["local_as"],
        "vrfs": vrf_list,
        "interface_count": len(data["interfaces"]),
        "ipv6_address_count": ipv6_count,
        "ipv6_source": data.get("ipv6_source", "snmp"),
        "bgp_peer_count": len(data["bgp"]["peers"]),
        "bgp_ipv6_source": data.get("bgp_ipv6_source", "snmp"),
        "vrf_count": len(vrf_list),
        "history_batch_id": _batch_id,
        "history_event_count": len(hist_rows),
    }
