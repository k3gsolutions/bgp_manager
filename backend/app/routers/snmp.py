"""
Router SNMP: coleta interfaces, BGP e VRFs via SNMP e persiste no banco.
"""
import asyncio
from datetime import datetime, timezone
import ipaddress
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..crypto import decrypt
from ..database import get_db
from ..deps.auth import CurrentUserCtx, get_device_for_user, require_permission
from ..models import BGPPeer, Device, Interface, InterfaceMetric, InventoryHistory
from ..schemas import (
    BgpCustomerReceivedRequest,
    BgpCustomerReceivedResponse,
    BgpProviderAdvertisedRequest,
    BgpProviderAdvertisedResponse,
    BGPPeerRoleUpdate,
    InventoryHistoryItem,
)
from ..services.bgp_customer_received_routes import run_huawei_customer_peer_received_routes
from ..services.bgp_peer_resolve import resolve_peer_local_and_name
from ..services.bgp_provider_advertised_routes import run_huawei_provider_peer_advertised_routes
from ..services.inventory_history import record_bgp_peer_role_change
from ..services.inventory_persist import is_ibgp_session
from ..services.route_policy_circuit import circuit_id_from_peer_policies
from ..services.snmp_inventory import persist_snmp_inventory
from ..services.snmp_status_refresh import persist_snmp_status_refresh
from ..snmp_collector import async_collect_bgp, async_collect_interfaces

router = APIRouter(prefix="/api/devices", tags=["snmp"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────
async def _get_device(device_id: int, db: AsyncSession, user: CurrentUserCtx) -> Device:
    return await get_device_for_user(device_id, db, user)


def _bgp_peer_row_display_name(p: BGPPeer, peer_name: str | None) -> str:
    """
    Coluna NOME na aba BGP: para Operadora/IX/CDN, prefixa ``Cxx-`` ao nome já resolvido
    (interfaces) quando o ID de circuito é único nas route-policies import/export.
    """
    base = (peer_name or "").strip()
    if not (p.is_provider or p.is_ix or p.is_cdn):
        return base or "—"
    cid = circuit_id_from_peer_policies(
        getattr(p, "route_policy_import", None),
        getattr(p, "route_policy_export", None),
    )
    if not cid:
        return base or "—"
    tail = base or (p.peer_ip or "").strip() or "—"
    return f"C{cid}-{tail}"


# ── Coleta completa (interfaces + BGP + VRFs) ──────────────────────────────
@router.post("/{device_id}/snmp/collect")
async def collect_all(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.snmp_collect"),
):
    """Coleta interfaces + BGP + VRFs via SNMP e persiste no banco."""
    log: list[str] = []
    device = await _get_device(device_id, db, user)
    emit(log, f"Coleta SNMP solicitada: device_id={device_id} ip={device.ip_address}")

    if not device.snmp_community:
        emit(log, "Erro: community SNMP não configurada para este dispositivo.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Community SNMP não configurada para este dispositivo",
        )

    try:
        body = await persist_snmp_inventory(
            db, device_id, device, log, source="snmp_collect"
        )
    except Exception as e:
        emit(log, f"Falha SNMP: {e!s}")
        raise HTTPException(status_code=502, detail=f"Erro SNMP: {e}") from e

    return {**body, "log": log}


@router.post("/{device_id}/snmp/status-refresh")
async def refresh_snmp_status(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.snmp_refresh"),
):
    """Atualiza só admin/oper das interfaces e estado/contadores BGP (sem full sync / histórico)."""
    log: list[str] = []
    device = await _get_device(device_id, db, user)
    emit(log, f"SNMP status-refresh: device_id={device_id} ip={device.ip_address}")

    if not device.snmp_community:
        emit(log, "Erro: community SNMP não configurada.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Community SNMP não configurada para este dispositivo",
        )

    try:
        body = await persist_snmp_status_refresh(db, device_id, device, log)
    except Exception as e:
        emit(log, f"Falha SNMP status-refresh: {e!s}")
        raise HTTPException(status_code=502, detail=f"Erro SNMP: {e}") from e

    return {**body, "log": log}


@router.get("/{device_id}/inventory-history", response_model=List[InventoryHistoryItem])
async def list_inventory_history(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("logs.view"),
    limit: int = 200,
    entity_type: Optional[str] = None,
):
    """Histórico de inserções, remoções e alterações do inventário coletado (SNMP)."""
    await _get_device(device_id, db, user)
    lim = max(1, min(limit, 500))
    stmt = select(InventoryHistory).where(InventoryHistory.device_id == device_id)
    if entity_type:
        stmt = stmt.where(InventoryHistory.entity_type == entity_type)
    stmt = stmt.order_by(InventoryHistory.created_at.desc()).limit(lim)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Leitura de interfaces do banco ─────────────────────────────────────────
@router.get("/{device_id}/interfaces")
async def list_interfaces(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("interfaces.view"),
):
    """Retorna interfaces persistidas no banco + última métrica."""
    await _get_device(device_id, db, user)

    result = await db.execute(
        select(Interface).where(Interface.device_id == device_id).order_by(Interface.name)
    )
    interfaces = result.scalars().all()
    peer_res = await db.execute(
        select(BGPPeer).where(BGPPeer.device_id == device_id).order_by(BGPPeer.peer_ip)
    )
    peers = peer_res.scalars().all()

    out = []
    for iface in interfaces:
        # Última métrica
        m_result = await db.execute(
            select(InterfaceMetric)
            .where(InterfaceMetric.interface_id == iface.id)
            .order_by(InterfaceMetric.timestamp.desc())
            .limit(1)
        )
        metric = m_result.scalar_one_or_none()

        ipv4_cidr = None
        mask = iface.netmask
        if mask and len(mask) == 4 and "." not in mask:
            mask = ".".join(str(ord(ch)) for ch in mask)

        if iface.ip_address and "/" in iface.ip_address:
            try:
                ipv4_cidr = str(ipaddress.ip_interface(iface.ip_address))
            except ValueError:
                ipv4_cidr = iface.ip_address
        elif iface.ip_address and mask:
            try:
                ipv4_cidr = str(ipaddress.ip_interface(f"{iface.ip_address}/{mask}"))
            except ValueError:
                ipv4_cidr = iface.ip_address
        elif iface.ip_address:
            ipv4_cidr = iface.ip_address

        ipv6_list = [x.strip() for x in (iface.ipv6_addresses or "").split(",") if x.strip()]
        related_peers = []
        if ipv4_cidr:
            try:
                net = ipaddress.ip_interface(ipv4_cidr).network
                for p in peers:
                    try:
                        pip = ipaddress.ip_address(p.peer_ip)
                    except ValueError:
                        continue
                    if pip.version == 4 and pip in net:
                        role = "customer"
                        if p.is_provider:
                            role = "provider"
                        elif p.is_ix:
                            role = "ix"
                        elif p.is_cdn:
                            role = "cdn"
                        related_peers.append(
                            {
                                "peer_ip": p.peer_ip,
                                "vrf_name": (getattr(p, "vrf_name", None) or "").strip(),
                                "remote_asn": p.remote_asn,
                                "role": role,
                            }
                        )
            except ValueError:
                pass

        out.append({
            "id": iface.id,
            "name": iface.name,
            "description": iface.description,
            "ip_address": iface.ip_address,
            "netmask": mask,
            "ipv4_cidr": ipv4_cidr,
            "ipv6_addresses": ipv6_list,
            "related_peers": related_peers,
            "is_active": iface.is_active,
            "deactivated_at": iface.deactivated_at.isoformat() if iface.deactivated_at else None,
            "admin_status": iface.admin_status,
            "status": iface.status,
            "speed_mbps": iface.speed_mbps,
            "last_updated": iface.last_updated.isoformat() if iface.last_updated else None,
            "in_octets": metric.in_octets if metric else None,
            "out_octets": metric.out_octets if metric else None,
        })

    return out


# ── Leitura de BGP peers do banco ──────────────────────────────────────────
@router.get("/{device_id}/bgp-peers")
async def list_bgp_peers(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.view"),
):
    """Retorna peers BGP persistidos no banco."""
    device = await _get_device(device_id, db, user)

    iface_res = await db.execute(
        select(Interface).where(Interface.device_id == device_id, Interface.is_active.is_(True))
    )
    interfaces = list(iface_res.scalars().all())

    result = await db.execute(
        select(BGPPeer)
        .where(BGPPeer.device_id == device_id)
        .order_by(BGPPeer.vrf_name, BGPPeer.peer_ip)
    )
    peers = result.scalars().all()

    out = []
    for p in peers:
        local_addr, peer_name = resolve_peer_local_and_name(p.peer_ip, p.local_addr, interfaces)
        out.append(
            {
            "id": p.id,
            "peer_ip": p.peer_ip,
            "vrf_name": (getattr(p, "vrf_name", None) or "").strip(),
            "remote_asn": p.remote_asn,
            "local_addr": local_addr,
            "peer_name": peer_name,
            "peer_display_name": _bgp_peer_row_display_name(p, peer_name),
            "route_policy_import": getattr(p, "route_policy_import", None),
            "route_policy_export": getattr(p, "route_policy_export", None),
            "in_updates": p.in_updates,
            "out_updates": p.out_updates,
            "uptime_secs": p.uptime_secs,
            "status": p.status,
            "is_customer": p.is_customer,
            "is_provider": p.is_provider,
            "is_ix": p.is_ix,
            "is_cdn": p.is_cdn,
            "is_ibgp": p.is_ibgp,
            "is_active": p.is_active,
            "deactivated_at": p.deactivated_at.isoformat() if p.deactivated_at else None,
            "device_local_asn": device.local_asn,
            "last_updated": p.last_updated.isoformat() if p.last_updated else None,
        }
        )
    return out


@router.post(
    "/{device_id}/bgp/provider-advertised-routes",
    response_model=BgpProviderAdvertisedResponse,
)
async def bgp_provider_advertised_routes(
    device_id: int,
    payload: BgpProviderAdvertisedRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.view"),
):
    """
    SSH Huawei: prefixos advertidos ao peer (Operadora, IX ou CDN), AS-Path na coluna Path/Ogn
    (``advertised-routes``). Paginação 20 em 20; acima de 200 rotas avisa e trunca a 200.
    """
    device = await _get_device(device_id, db, user)
    if (device.vendor or "").strip().lower() != "huawei":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Disponível apenas para vendor Huawei (VRP).",
        )
    if not device.username or not device.password_encrypted:
        raise HTTPException(status_code=422, detail="Credenciais SSH não configuradas.")

    res = await db.execute(
        select(BGPPeer).where(BGPPeer.id == payload.peer_id, BGPPeer.device_id == device_id)
    )
    peer = res.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer BGP não encontrado neste equipamento.")
    if not (peer.is_provider or peer.is_ix or peer.is_cdn):
        raise HTTPException(
            status_code=422,
            detail="Este endpoint é para peers classificados como Operadora, IX ou CDN.",
        )
    if not peer.is_active:
        raise HTTPException(status_code=422, detail="Peer inativo no inventário — ajuste ou reative antes.")

    log: list[str] = []
    emit(
        log,
        f"SSH advertised list: peer_id={payload.peer_id} ip={peer.peer_ip!r} vrf={(peer.vrf_name or '')!r} offset={payload.offset}",
    )
    try:
        password = decrypt(device.password_encrypted)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(
            status_code=422,
            detail=(
                "Não foi possível descriptografar credenciais do dispositivo. "
                "Configure FERNET_KEY correta no backend para este banco de dados."
            ),
        ) from e
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        return run_huawei_provider_peer_advertised_routes(
            host=device.ip_address,
            port=device.ssh_port,
            username=device.username,
            password=password,
            vendor=device.vendor or "Huawei",
            peer_ip=peer.peer_ip,
            vrf_name=(peer.vrf_name or "").strip(),
            offset=payload.offset,
            fetch_all=bool(payload.fetch_all),
            log=log,
        )

    try:
        body = await loop.run_in_executor(None, _run)
    except Exception as e:
        emit(log, f"Erro SSH advertised list: {e!s}")
        raise HTTPException(status_code=502, detail=str(e)) from e

    if body.get("error") == "ssh":
        raise HTTPException(status_code=502, detail=body.get("message") or "Falha SSH")
    if body.get("error") == "vendor":
        raise HTTPException(status_code=422, detail=body.get("message") or "Vendor inválido")

    return BgpProviderAdvertisedResponse.model_validate(body)


@router.post(
    "/{device_id}/bgp/customer-received-routes",
    response_model=BgpCustomerReceivedResponse,
)
async def bgp_customer_received_routes(
    device_id: int,
    payload: BgpCustomerReceivedRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.view"),
):
    """
    SSH Huawei: prefixos **recebidos** do peer Cliente (`is_customer`),
    comando ``display bgp routing-table peer <ip> received-routes`` (e variantes VRF/IPv6).
    Paginação 20 em 20; acima de 200 rotas avisa e trunca a 200.
    """
    device = await _get_device(device_id, db, user)
    if (device.vendor or "").strip().lower() != "huawei":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Disponível apenas para vendor Huawei (VRP).",
        )
    if not device.username or not device.password_encrypted:
        raise HTTPException(status_code=422, detail="Credenciais SSH não configuradas.")

    res = await db.execute(
        select(BGPPeer).where(BGPPeer.id == payload.peer_id, BGPPeer.device_id == device_id)
    )
    peer = res.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer BGP não encontrado neste equipamento.")
    if not peer.is_customer:
        raise HTTPException(
            status_code=422,
            detail="Este endpoint é apenas para peers classificados como Cliente (is_customer).",
        )
    if not peer.is_active:
        raise HTTPException(status_code=422, detail="Peer inativo no inventário — ajuste ou reative antes.")

    log: list[str] = []
    emit(
        log,
        f"SSH received list: peer_id={payload.peer_id} ip={peer.peer_ip!r} vrf={(peer.vrf_name or '')!r} offset={payload.offset}",
    )
    try:
        password = decrypt(device.password_encrypted)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(
            status_code=422,
            detail=(
                "Não foi possível descriptografar credenciais do dispositivo. "
                "Configure FERNET_KEY correta no backend para este banco de dados."
            ),
        ) from e
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        return run_huawei_customer_peer_received_routes(
            host=device.ip_address,
            port=device.ssh_port,
            username=device.username,
            password=password,
            vendor=device.vendor or "Huawei",
            peer_ip=peer.peer_ip,
            vrf_name=(peer.vrf_name or "").strip(),
            offset=payload.offset,
            fetch_all=bool(payload.fetch_all),
            log=log,
        )

    try:
        body = await loop.run_in_executor(None, _run)
    except Exception as e:
        emit(log, f"Erro SSH received-routes list: {e!s}")
        raise HTTPException(status_code=502, detail=str(e)) from e

    if body.get("error") == "ssh":
        raise HTTPException(status_code=502, detail=body.get("message") or "Falha SSH")
    if body.get("error") == "vendor":
        raise HTTPException(status_code=422, detail=body.get("message") or "Vendor inválido")

    return BgpCustomerReceivedResponse.model_validate(body)


@router.patch("/{device_id}/bgp-peers/{peer_id}/deactivate")
async def deactivate_bgp_peer(
    device_id: int,
    peer_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.edit_role"),
):
    """Desativação lógica de peer BGP (mantém histórico no banco)."""
    await _get_device(device_id, db, user)
    result = await db.execute(
        select(BGPPeer).where(BGPPeer.id == peer_id, BGPPeer.device_id == device_id)
    )
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer BGP não encontrado")

    peer.is_active = False
    peer.deactivated_at = _now()
    await db.flush()
    await db.refresh(peer)
    return {
        "id": peer.id,
        "peer_ip": peer.peer_ip,
        "vrf_name": (getattr(peer, "vrf_name", None) or "").strip(),
        "is_active": peer.is_active,
        "deactivated_at": peer.deactivated_at.isoformat() if peer.deactivated_at else None,
    }


@router.delete("/{device_id}/bgp-peers/{peer_id}")
async def delete_bgp_peer(
    device_id: int,
    peer_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.edit_role"),
):
    """Remove o registro do peer BGP deste equipamento (inventário)."""
    await _get_device(device_id, db, user)
    result = await db.execute(
        select(BGPPeer).where(BGPPeer.id == peer_id, BGPPeer.device_id == device_id)
    )
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer BGP não encontrado")
    await db.delete(peer)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{device_id}/bgp-peers/{peer_id}")
async def patch_bgp_peer_role(
    device_id: int,
    peer_id: int,
    payload: BGPPeerRoleUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.edit_role"),
):
    """Define manualmente o papel do peer (cliente/operadora/ix/cdn)."""
    await _get_device(device_id, db, user)
    result = await db.execute(
        select(BGPPeer).where(BGPPeer.id == peer_id, BGPPeer.device_id == device_id)
    )
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer BGP não encontrado")

    old_c, old_p = peer.is_customer, peer.is_provider
    old_ix, old_cdn = peer.is_ix, peer.is_cdn
    if (
        old_c,
        old_p,
        old_ix,
        old_cdn,
    ) != (
        payload.is_customer,
        payload.is_provider,
        payload.is_ix,
        payload.is_cdn,
    ):
        await record_bgp_peer_role_change(
            db,
            device_id,
            peer.peer_ip,
            old_c,
            old_p,
            old_ix,
            old_cdn,
            payload.is_customer,
            payload.is_provider,
            payload.is_ix,
            payload.is_cdn,
            vrf_name=(getattr(peer, "vrf_name", None) or "").strip(),
            source="peer_role",
        )

    peer.is_customer = payload.is_customer
    peer.is_provider = payload.is_provider
    peer.is_ix = payload.is_ix
    peer.is_cdn = payload.is_cdn
    await db.flush()
    await db.refresh(peer)
    iface_res = await db.execute(
        select(Interface).where(Interface.device_id == device_id, Interface.is_active.is_(True))
    )
    interfaces = list(iface_res.scalars().all())
    local_addr, peer_name = resolve_peer_local_and_name(peer.peer_ip, peer.local_addr, interfaces)
    return {
        "id": peer.id,
        "peer_ip": peer.peer_ip,
        "vrf_name": (getattr(peer, "vrf_name", None) or "").strip(),
        "local_addr": local_addr,
        "peer_name": peer_name,
        "peer_display_name": _bgp_peer_row_display_name(peer, peer_name),
        "route_policy_import": getattr(peer, "route_policy_import", None),
        "route_policy_export": getattr(peer, "route_policy_export", None),
        "is_customer": peer.is_customer,
        "is_provider": peer.is_provider,
        "is_ix": peer.is_ix,
        "is_cdn": peer.is_cdn,
        "is_ibgp": peer.is_ibgp,
        "is_active": peer.is_active,
    }


@router.patch("/{device_id}/interfaces/{interface_id}/deactivate")
async def deactivate_interface(
    device_id: int,
    interface_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.edit"),
):
    """Desativação lógica de interface (mantém histórico no banco)."""
    await _get_device(device_id, db, user)
    result = await db.execute(
        select(Interface).where(Interface.id == interface_id, Interface.device_id == device_id)
    )
    iface = result.scalar_one_or_none()
    if not iface:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interface não encontrada")

    iface.is_active = False
    iface.deactivated_at = _now()
    await db.flush()
    await db.refresh(iface)
    return {
        "id": iface.id,
        "name": iface.name,
        "is_active": iface.is_active,
        "deactivated_at": iface.deactivated_at.isoformat() if iface.deactivated_at else None,
    }


# ── Coleta live (sem persistir) ────────────────────────────────────────────
@router.get("/{device_id}/snmp/interfaces/live")
async def live_interfaces(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("interfaces.view"),
):
    """Coleta interfaces via SNMP em tempo real (não persiste)."""
    device = await _get_device(device_id, db, user)
    if not device.snmp_community:
        raise HTTPException(422, "Community SNMP não configurada")
    try:
        return await async_collect_interfaces(device.ip_address, device.snmp_community)
    except Exception as e:
        raise HTTPException(502, f"Erro SNMP: {e}")


@router.get("/{device_id}/snmp/bgp/live")
async def live_bgp(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.view"),
):
    """Coleta peers BGP via SNMP em tempo real (não persiste)."""
    device = await _get_device(device_id, db, user)
    if not device.snmp_community:
        raise HTTPException(422, "Community SNMP não configurada")
    try:
        raw = await async_collect_bgp(device.ip_address, device.snmp_community)
        la = raw.get("local_as")
        peers_out = []
        for p in raw.get("peers", []):
            ra = p.get("remote_as")
            peers_out.append(
                {
                    **p,
                    "is_ibgp": is_ibgp_session(la, ra),
                }
            )
        return {"local_as": la, "peers": peers_out}
    except Exception as e:
        raise HTTPException(502, f"Erro SNMP: {e}")
