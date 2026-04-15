import json
import re
import socket
import time
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..audit_log import log_user_consultation
from ..crypto import decrypt, encrypt
from ..database import get_db
from ..deps.auth import CurrentUserCtx, get_device_for_user, require_permission
from ..models import BGPPeer, Company, Configuration, Device, Interface, InventoryHistory, PrefixLookupHistory
from ..schemas import (
    BgpExportLookupRequest,
    BgpExportLookupResponse,
    BgpOperatorLocalPrefApplyRequest,
    BgpOperatorLocalPrefApplyResponse,
    BgpOperatorLocalPrefResponse,
    DeviceBatchImportFailure,
    DeviceBatchImportRequest,
    DeviceBatchImportResponse,
    DeviceConnectTest,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from ..services.bgp_export_lookup import run_huawei_bgp_export_lookup
from ..services.bgp_peer_resolve import build_peer_hints_from_db, resolve_peer_local_and_name
from ..services.bgp_peer_maintenance import purge_inactive_bgp_peers
from ..services.config_snapshot import persist_running_config_snapshot
from ..services.huawei_ssh_inventory import persist_huawei_cli_inventory
from ..services.route_policy_local_pref import parse_route_policy_local_preference
from ..services.snmp_inventory import persist_snmp_inventory

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _normalize_lookup_query(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    if "/" in s:
        return s
    up = s.upper()
    if up.startswith("AS"):
        return up
    return s


_MAX_LOOKUP_RESULT_JSON = 400_000


def _safe_json_dumps(obj: object, *, max_len: int = _MAX_LOOKUP_RESULT_JSON) -> str:
    """Serializa para SQLite; nunca levanta (substitui tipos não-JSON)."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = json.dumps({"_serialization_error": True, "repr": repr(obj)[:20_000]}, ensure_ascii=False)
    if len(s) > max_len:
        s = s[: max_len - 40] + "\n...(__truncado_por_tamanho__)..."
    return s


def _extract_local_pref_for_node(policy_text: str, *, node: int = 3010) -> int | None:
    """Lê o LocalPref do node alvo no output de `display route-policy`."""
    current_node: int | None = None
    rx_node = re.compile(r"^\s*route-policy\s+\S+\s+(?:permit|deny)\s+node\s+(\d+)\b", re.I)
    rx_apply = re.compile(r"^\s*apply\s+local-preference\s+(\d+)\b", re.I)
    for raw in (policy_text or "").splitlines():
        line = raw.strip()
        m_node = rx_node.match(line)
        if m_node:
            try:
                current_node = int(m_node.group(1))
            except (TypeError, ValueError):
                current_node = None
            continue
        if current_node != int(node):
            continue
        m_apply = rx_apply.match(line)
        if m_apply:
            try:
                return int(m_apply.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _extract_local_pref_for_policy_node_from_running_cfg(
    config_text: str,
    *,
    policy: str,
    node: int = 3010,
) -> int | None:
    """Extrai LocalPref no node alvo para uma policy no estilo running-config."""
    target_policy = (policy or "").strip().lower()
    current_policy: str | None = None
    current_node: int | None = None
    rx_policy = re.compile(r"^\s*route-policy\s+(\S+)\s+(?:permit|deny)\s+node\s+(\d+)\b", re.I)
    rx_apply = re.compile(r"^\s*apply\s+local-preference\s+(\d+)\b", re.I)
    for raw in (config_text or "").splitlines():
        line = raw.strip()
        if line == "#":
            current_policy = None
            current_node = None
            continue
        m_pol = rx_policy.match(line)
        if m_pol:
            current_policy = (m_pol.group(1) or "").strip().lower()
            try:
                current_node = int(m_pol.group(2))
            except (TypeError, ValueError):
                current_node = None
            continue
        if current_policy != target_policy or current_node != int(node):
            continue
        m_apply = rx_apply.match(line)
        if m_apply:
            try:
                return int(m_apply.group(1))
            except (TypeError, ValueError):
                return None
    return None


async def _record_prefix_lookup_history(
    db: AsyncSession,
    device_id: int,
    query: str,
    result: dict,
) -> None:
    advertised_min = [
        {
            "peer_ip": x.get("peer_ip"),
            "role": x.get("role"),
            "peer_name": x.get("peer_name"),
            "remote_asn": x.get("remote_asn"),
            "advertised_as_path": x.get("advertised_as_path"),
        }
        for x in (result.get("advertised_to") or [])
    ]
    row = PrefixLookupHistory(
        device_id=device_id,
        query=query.strip(),
        normalized_query=_normalize_lookup_query(query),
        route_found=bool(result.get("route_found")),
        from_peer_ip=result.get("from_peer_ip"),
        as_path=result.get("as_path"),
        origin=result.get("origin"),
        advertised_to_json=_safe_json_dumps(advertised_min, max_len=200_000),
        result_json=_safe_json_dumps(result, max_len=_MAX_LOOKUP_RESULT_JSON),
    )
    db.add(row)
    await db.flush()


async def _record_local_pref_change_history(
    db: AsyncSession,
    *,
    device_id: int,
    peer_id: int,
    peer_ip: str,
    route_policy_import: str,
    old_local_preference: int | None,
    new_local_preference: int,
    username: str,
) -> None:
    ev = InventoryHistory(
        device_id=device_id,
        source="manual_local_pref_apply",
        entity_type="route_policy_local_pref",
        action="update",
        entity_key=f"peer:{peer_id}|policy:{route_policy_import}|node:3010",
        old_json=_safe_json_dumps(
            {
                "peer_id": peer_id,
                "peer_ip": peer_ip,
                "route_policy_import": route_policy_import,
                "node": 3010,
                "local_preference": old_local_preference,
            },
            max_len=10_000,
        ),
        new_json=_safe_json_dumps(
            {
                "peer_id": peer_id,
                "peer_ip": peer_ip,
                "route_policy_import": route_policy_import,
                "node": 3010,
                "local_preference": new_local_preference,
                "changed_by": username,
            },
            max_len=10_000,
        ),
    )
    db.add(ev)
    await db.flush()


def _device_to_response(d: Device, company_name: str | None = None) -> DeviceResponse:
    return DeviceResponse(
        id=d.id,
        company_id=d.company_id,
        client=d.client,
        name=d.name,
        ip_address=d.ip_address,
        ssh_port=d.ssh_port,
        vendor=d.vendor,
        model=d.model,
        username=d.username,
        snmp_community=d.snmp_community,
        description=d.description,
        created_at=d.created_at,
        updated_at=d.updated_at,
        local_asn=d.local_asn,
        company_name=company_name,
    )


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.view"),
):
    stmt = (
        select(Device, Company.name)
        .join(Company, Device.company_id == Company.id, isouter=True)
        .order_by(Device.created_at.desc())
    )
    if not user.has_global_company_scope():
        if not user.company_ids:
            return []
        stmt = stmt.where(Device.company_id.in_(user.company_ids))
    rows = (await db.execute(stmt)).all()
    return [_device_to_response(d, name) for d, name in rows]


@router.post("/", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.create"),
):
    if not user.can_access_company(payload.company_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a esta empresa")
    # Verifica duplicidade de IP
    existing = await db.execute(select(Device).where(Device.ip_address == payload.ip_address))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dispositivo com IP {payload.ip_address} já cadastrado",
        )

    device = Device(
        company_id=payload.company_id,
        client=payload.client,
        name=payload.name,
        ip_address=payload.ip_address,
        ssh_port=payload.ssh_port,
        vendor=payload.vendor,
        model=payload.model,
        username=payload.username,
        password_encrypted=encrypt(payload.password),
        snmp_community=payload.snmp_community,
        description=payload.description,
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    cn = (
        await db.execute(select(Company.name).where(Company.id == device.company_id))
    ).scalar_one_or_none()
    return _device_to_response(device, cn)


def _format_validation_error(err: ValidationError) -> str:
    parts: list[str] = []
    for item in err.errors():
        loc = ".".join(str(x) for x in item.get("loc", ()))
        msg = item.get("msg", "")
        parts.append(f"{loc}: {msg}" if loc else str(msg))
    s = "; ".join(parts)
    return s[:800] if len(s) > 800 else s


@router.post("/batch", response_model=DeviceBatchImportResponse)
async def create_devices_batch(
    body: DeviceBatchImportRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.create"),
):
    """
    Cria vários dispositivos na mesma requisição. Cada linha é validada e inserida
    independentemente (SAVEPOINT): falhas não desfazem os já criados.
    """
    created: list[DeviceResponse] = []
    failed: list[DeviceBatchImportFailure] = []

    for index, raw in enumerate(body.devices):
        if not isinstance(raw, dict):
            failed.append(
                DeviceBatchImportFailure(
                    index=index,
                    detail="Cada item deve ser um objeto JSON",
                    ip_address=None,
                )
            )
            continue

        try:
            payload = DeviceCreate.model_validate(raw)
        except ValidationError as e:
            failed.append(
                DeviceBatchImportFailure(
                    index=index,
                    detail=_format_validation_error(e),
                    ip_address=raw.get("ip_address") if isinstance(raw.get("ip_address"), str) else None,
                )
            )
            continue

        if not user.can_access_company(payload.company_id):
            failed.append(
                DeviceBatchImportFailure(
                    index=index,
                    detail="Sem acesso a esta empresa (company_id)",
                    ip_address=payload.ip_address,
                )
            )
            continue

        dup = await db.execute(select(Device.id).where(Device.ip_address == payload.ip_address))
        if dup.scalar_one_or_none():
            failed.append(
                DeviceBatchImportFailure(
                    index=index,
                    detail=f"Dispositivo com IP {payload.ip_address} já cadastrado",
                    ip_address=payload.ip_address,
                )
            )
            continue

        device = Device(
            company_id=payload.company_id,
            client=payload.client,
            name=payload.name,
            ip_address=payload.ip_address,
            ssh_port=payload.ssh_port,
            vendor=payload.vendor,
            model=payload.model,
            username=payload.username,
            password_encrypted=encrypt(payload.password),
            snmp_community=payload.snmp_community,
            description=payload.description,
        )

        try:
            async with db.begin_nested():
                db.add(device)
                await db.flush()
                await db.refresh(device)
        except IntegrityError:
            failed.append(
                DeviceBatchImportFailure(
                    index=index,
                    detail="Conflito ao gravar (IP duplicado ou restrição do banco)",
                    ip_address=payload.ip_address,
                )
            )
            continue

        cn = (
            await db.execute(select(Company.name).where(Company.id == device.company_id))
        ).scalar_one_or_none()
        created.append(_device_to_response(device, cn))

    return DeviceBatchImportResponse(created=created, failed=failed)


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.view"),
):
    device = await _get_or_404(device_id, db, user)
    cn = (
        await db.execute(select(Company.name).where(Company.id == device.company_id))
    ).scalar_one_or_none()
    return _device_to_response(device, cn)


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.edit"),
):
    device = await _get_or_404(device_id, db, user)
    if not user.can_access_company(payload.company_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a esta empresa")

    # Verifica conflito de IP se foi alterado
    if payload.ip_address and payload.ip_address != device.ip_address:
        existing = await db.execute(
            select(Device).where(Device.ip_address == payload.ip_address)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"IP {payload.ip_address} já pertence a outro dispositivo",
            )

    update_data = payload.model_dump(exclude_unset=True)
    if "password" in update_data:
        device.password_encrypted = encrypt(update_data.pop("password"))

    for field, value in update_data.items():
        setattr(device, field, value)

    await db.flush()
    await db.refresh(device)
    cn = (
        await db.execute(select(Company.name).where(Company.id == device.company_id))
    ).scalar_one_or_none()
    return _device_to_response(device, cn)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.delete"),
):
    device = await _get_or_404(device_id, db, user)
    await db.delete(device)


@router.post("/{device_id}/maintenance/purge-inactive-bgp-peers")
async def maintenance_purge_inactive_bgp_peers(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.edit"),
):
    """Remove do banco apenas peers BGP com `is_active=false` (mantém ativos e histórico útil)."""
    if not user.is_superadmin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas superadmin pode executar a remoção de peers BGP inativos.",
        )
    device = await _get_or_404(device_id, db, user)
    deleted = await purge_inactive_bgp_peers(db, device_id)
    return {
        "device_id": device_id,
        "device_name": device.name,
        "inactive_bgp_peers_deleted": deleted,
    }


@router.post("/{device_id}/ssh/collect-huawei")
async def ssh_collect_huawei(
    device_id: int,
    purge_inactive_bgp_first: bool = Query(
        False,
        description="Se true, apaga peers BGP inativos do banco antes da coleta (só tem efeito para utilizador superadmin).",
    ),
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.ssh_collect"),
):
    """
    Coleta completa via SSH (comandos VRP Huawei — alinhado a netops_netbox_sync).
    Apenas fabricante Huawei. Inclui BGP por VPN-instance (`collect_bgp_all_vrfs`).
    """
    device = await _get_or_404(device_id, db, user)
    log: list[str] = []
    purge_first = bool(purge_inactive_bgp_first) and user.is_superadmin()
    if purge_inactive_bgp_first and not purge_first:
        emit(log, "purge_inactive_bgp_first ignorado: apenas superadmin pode remover peers BGP inativos antes da coleta.")
    try:
        if purge_first:
            n = await purge_inactive_bgp_peers(db, device.id)
            emit(log, f"Manutenção: removidos {n} peer(s) BGP inativo(s) antes da coleta SSH.")
        body = await persist_huawei_cli_inventory(
            db,
            device.id,
            device,
            log,
            source="api_ssh_collect",
        )
        return {**body, "log": log}
    except ValueError as e:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(e), "log": log})
    except Exception as e:
        emit(log, f"Erro na coleta SSH Huawei: {e!s}")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(e), "log": log},
        )


@router.post("/{device_id}/ssh/bgp-export-lookup", response_model=BgpExportLookupResponse)
async def ssh_bgp_export_lookup(
    device_id: int,
    payload: BgpExportLookupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.lookup"),
):
    """
    SSH Huawei: consulta `display bgp routing-table` por IP/prefixo ou ASN,
    interpreta AS-Path (prepend) e communities; testa advertised-routes para peers operadora (banco).
    """
    import asyncio

    device = await _get_or_404(device_id, db, user)
    if (device.vendor or "").strip().lower() != "huawei":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consulta de export BGP via SSH disponível apenas para vendor Huawei (VRP).",
        )

    q_preview = (payload.query or "").strip()[:500]
    log_user_consultation(
        user_id=user.id,
        username=user.username,
        role=user.role,
        consultation="bgp_export_lookup",
        device_id=device_id,
        detail={"query": q_preview},
        client_ip=request.client.host if request.client else None,
    )

    log: list[str] = []
    emit(log, f"BGP export lookup: device_id={device_id} query={payload.query!r}")

    iface_res = await db.execute(
        select(Interface).where(Interface.device_id == device_id, Interface.is_active.is_(True))
    )
    interfaces = list(iface_res.scalars().all())
    peer_rows = await db.execute(
        select(BGPPeer).where(
            BGPPeer.device_id == device_id,
            BGPPeer.is_active.is_(True),
        )
    )
    peers = list(peer_rows.scalars().all())
    peer_hints = build_peer_hints_from_db(peers, interfaces)
    operator_peers = [
        {
            "peer_ip": p.peer_ip,
            "vrf_name": (getattr(p, "vrf_name", None) or "").strip(),
            "remote_asn": p.remote_asn,
            "peer_name": peer_hints.get(p.peer_ip, {}).get("display_name", p.peer_ip),
            "role": (
                "provider" if p.is_provider else
                "ix" if p.is_ix else
                "cdn" if p.is_cdn else
                "customer"
            ),
            "is_provider": p.is_provider,
            "is_ix": p.is_ix,
            "is_customer": p.is_customer,
            "is_cdn": p.is_cdn,
        }
        for p in peers
    ]
    password = decrypt(device.password_encrypted)
    loop = asyncio.get_running_loop()

    def _run() -> dict:
        return run_huawei_bgp_export_lookup(
            host=device.ip_address,
            port=device.ssh_port,
            username=device.username,
            password=password,
            vendor=device.vendor or "Huawei",
            query=payload.query.strip(),
            local_asn=device.local_asn,
            operator_peers=operator_peers,
            peer_hints=peer_hints,
            log=log,
        )

    try:
        body = await loop.run_in_executor(None, _run)
    except Exception as e:
        emit(log, f"Erro SSH na consulta BGP: {e!s}")
        raise HTTPException(status_code=502, detail=str(e)) from e

    body["operator_peers"] = operator_peers
    try:
        await _record_prefix_lookup_history(db, device_id, payload.query, body)
    except Exception as hist_e:
        emit(log, f"prefix_lookup_history: não gravado (consulta segue): {hist_e!s}")
    return BgpExportLookupResponse(**body)


@router.get("/{device_id}/bgp/operator-local-pref", response_model=BgpOperatorLocalPrefResponse)
async def bgp_operator_local_pref(
    device_id: int,
    force_refresh: bool = Query(False, description="Quando true, força coleta SSH de running-config."),
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("bgp.view"),
):
    """
    Relação de Operadoras (peers classificados como provider) e LocalPref
    derivado da route-policy de import no último backup de configuração.
    """
    device = await _get_or_404(device_id, db, user)
    source = "backup_config"
    if force_refresh:
        import asyncio
        from netmiko import ConnectHandler

        if (device.vendor or "").strip().lower() != "huawei":
            raise HTTPException(status_code=400, detail="Atualização forçada suportada apenas para Huawei")

        password = decrypt(device.password_encrypted)
        device_params = {
            "device_type": _vendor_to_netmiko(device.vendor),
            "host": device.ip_address,
            "port": device.ssh_port,
            "username": device.username,
            "password": password,
            "timeout": 60,
            "auth_timeout": 30,
            "fast_cli": False,
        }

        def _run_fetch_cfg() -> str:
            conn = None
            try:
                conn = ConnectHandler(**device_params)
                return conn.send_command_timing(
                    "display current-configuration",
                    read_timeout=120,
                    strip_prompt=False,
                    strip_command=False,
                )
            finally:
                if conn:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass

        try:
            loop = asyncio.get_running_loop()
            fresh_cfg = await loop.run_in_executor(None, _run_fetch_cfg)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Falha na coleta SSH do LocalPref: {e!s}") from e

        refresh_log: list[str] = []
        await persist_running_config_snapshot(
            db,
            device_id=device_id,
            device=device,
            log=refresh_log,
            config_text=fresh_cfg or "",
            source="ssh_operator_local_pref_refresh",
        )
        await db.commit()
        source = "live_ssh_refresh"

    cfg_res = await db.execute(
        select(Configuration)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    cfg = cfg_res.scalar_one_or_none()
    policy_lp: dict[str, int] = parse_route_policy_local_preference(cfg.config_text) if cfg else {}

    iface_res = await db.execute(
        select(Interface).where(Interface.device_id == device_id, Interface.is_active.is_(True))
    )
    interfaces = list(iface_res.scalars().all())
    peer_res = await db.execute(
        select(BGPPeer)
        .where(
            BGPPeer.device_id == device_id,
            BGPPeer.is_active.is_(True),
            BGPPeer.is_provider.is_(True),
        )
        .order_by(BGPPeer.peer_ip)
    )
    peers = list(peer_res.scalars().all())

    items: list[dict] = []
    for p in peers:
        local_addr, peer_name = resolve_peer_local_and_name(p.peer_ip, p.local_addr, interfaces)
        _ = local_addr  # sem uso no payload; mantido para consistência de resolução.
        pol = (getattr(p, "route_policy_import", None) or "").strip() or None
        lp = policy_lp.get(pol) if pol else None
        items.append(
            {
                "peer_id": p.id,
                "peer_ip": p.peer_ip,
                "vrf_name": (getattr(p, "vrf_name", None) or "").strip(),
                "peer_name": peer_name,
                "route_policy_import": pol,
                "local_preference": lp,
            }
        )

    # Ordem de preferência: maior LocalPref primeiro; sem valor ficam no fim.
    items.sort(key=lambda x: (x["local_preference"] is None, -(x["local_preference"] or -1), x["peer_ip"]))
    return {
        "collected_at": cfg.collected_at if cfg else None,
        "source": source,
        "items": items,
    }


@router.post(
    "/{device_id}/bgp/operator-local-pref/apply",
    response_model=BgpOperatorLocalPrefApplyResponse,
)
async def bgp_operator_local_pref_apply(
    device_id: int,
    payload: BgpOperatorLocalPrefApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.edit"),
):
    if not payload.confirm_impact:
        raise HTTPException(
            status_code=400,
            detail="Confirmação obrigatória: marque ciência de impacto para aplicar.",
        )
    device = await _get_or_404(device_id, db, user)
    peer_res = await db.execute(
        select(BGPPeer).where(
            BGPPeer.id == payload.peer_id,
            BGPPeer.device_id == device_id,
            BGPPeer.is_active.is_(True),
            BGPPeer.is_provider.is_(True),
        )
    )
    peer = peer_res.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Peering de Operadora não encontrado")
    policy = (getattr(peer, "route_policy_import", None) or "").strip()
    if not policy:
        raise HTTPException(status_code=400, detail="Peer sem route-policy de import para editar")

    cfg_res = await db.execute(
        select(Configuration)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    cfg = cfg_res.scalar_one_or_none()
    old_local_preference = None
    if cfg and cfg.config_text:
        old_local_preference = parse_route_policy_local_preference(cfg.config_text).get(policy)

    import asyncio
    from netmiko import ConnectHandler

    password = decrypt(device.password_encrypted)
    device_params = {
        "device_type": _vendor_to_netmiko(device.vendor),
        "host": device.ip_address,
        "port": device.ssh_port,
        "username": device.username,
        "password": password,
        "timeout": 60,
        "conn_timeout": 20,
        "banner_timeout": 45,
        "auth_timeout": 30,
        "fast_cli": False,
    }

    def _run_apply() -> dict:
        # Pré-check TCP deixa o erro mais assertivo quando há bloqueio de rede/firewall.
        try:
            with socket.create_connection((device.ip_address, int(device.ssh_port)), timeout=5):
                pass
        except OSError as tcp_e:
            raise RuntimeError(
                f"Pré-check TCP falhou em {device.ip_address}:{device.ssh_port} ({tcp_e!s})"
            ) from tcp_e

        conn = None
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                conn = ConnectHandler(**device_params)
                break
            except Exception as e:
                last_err = e
                if attempt >= 3:
                    raise RuntimeError(
                        "SSH falhou após 3 tentativas "
                        f"({device.ip_address}:{device.ssh_port}): {e!s}"
                    ) from e
                time.sleep(1.0 * attempt)
        try:
            chunks: list[str] = []
            # Huawei VRP: alteração em configuração requer commit explícito.
            chunks.append(
                conn.send_command_timing("system-view", strip_prompt=False, strip_command=False)
            )
            chunks.append(
                conn.send_command_timing(
                    f"route-policy {policy} permit node 3010",
                    strip_prompt=False,
                    strip_command=False,
                )
            )
            chunks.append(
                conn.send_command_timing(
                    f"apply local-preference {payload.new_local_preference}",
                    strip_prompt=False,
                    strip_command=False,
                )
            )
            chunks.append(conn.send_command_timing("quit", strip_prompt=False, strip_command=False))
            chunks.append(conn.send_command_timing("commit", read_timeout=90, strip_prompt=False, strip_command=False))
            chunks.append(conn.send_command_timing("quit", strip_prompt=False, strip_command=False))

            verify_text = conn.send_command_timing(
                f"display route-policy {policy}",
                read_timeout=90,
                strip_prompt=False,
                strip_command=False,
            )
            chunks.append("\n--- verify display route-policy ---\n")
            chunks.append(verify_text or "")
            applied_value = _extract_local_pref_for_node(verify_text, node=3010)
            if applied_value is None:
                verify_cfg = conn.send_command_timing(
                    f"display current-configuration | begin route-policy {policy}",
                    read_timeout=90,
                    strip_prompt=False,
                    strip_command=False,
                )
                chunks.append("\n--- verify running-config begin route-policy ---\n")
                chunks.append(verify_cfg or "")
                applied_value = _extract_local_pref_for_policy_node_from_running_cfg(
                    verify_cfg,
                    policy=policy,
                    node=3010,
                )
            if applied_value != payload.new_local_preference:
                raise RuntimeError(
                    "Verificação pós-commit divergente no node 3010 "
                    f"(esperado={payload.new_local_preference}, lido={applied_value})"
                )
            post_cfg = conn.send_command_timing(
                "display current-configuration",
                read_timeout=120,
                strip_prompt=False,
                strip_command=False,
            )
            chunks.append("\n--- post-apply running-config snapshot ---\n")
            chunks.append(post_cfg or "")
            return {
                "output": "".join(chunks),
                "applied_value": applied_value,
                "running_config": post_cfg or "",
            }
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass

    try:
        loop = asyncio.get_running_loop()
        run_data = await loop.run_in_executor(None, _run_apply)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao aplicar no dispositivo: {e!s}") from e

    apply_log: list[str] = []
    running_cfg = (run_data.get("running_config") or "").strip()
    if running_cfg:
        await persist_running_config_snapshot(
            db,
            device_id=device_id,
            device=device,
            log=apply_log,
            config_text=running_cfg,
            source="ssh_local_pref_apply",
        )
    confirmed_local_pref = parse_route_policy_local_preference(running_cfg).get(policy) if running_cfg else None
    if confirmed_local_pref is None:
        confirmed_local_pref = run_data.get("applied_value")
    if confirmed_local_pref is None:
        raise HTTPException(
            status_code=502,
            detail="Alteração aplicada, mas sem confirmação final no snapshot pós-apply.",
        )

    await _record_local_pref_change_history(
        db,
        device_id=device_id,
        peer_id=peer.id,
        peer_ip=peer.peer_ip,
        route_policy_import=policy,
        old_local_preference=old_local_preference,
        new_local_preference=int(confirmed_local_pref),
        username=user.username,
    )
    await db.commit()
    return {
        "peer_id": peer.id,
        "peer_ip": peer.peer_ip,
        "route_policy_import": policy,
        "node": 3010,
        "old_local_preference": old_local_preference,
        "new_local_preference": int(confirmed_local_pref),
        "applied": True,
        "output": ((run_data.get("output") or "") + "\n" + "\n".join(apply_log))[:8000],
        "updated_at": datetime.now(timezone.utc),
    }


@router.post("/{device_id}/test-connection", response_model=DeviceConnectTest)
async def test_connection(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("devices.test_connection"),
):
    """Abre sessão SSH (Netmiko), detecta prompt e encerra — teste real de conectividade."""
    device = await _get_or_404(device_id, db, user)
    log: list[str] = []
    label = device.name or device.ip_address
    netmiko_type = _vendor_to_netmiko(device.vendor)
    emit(log, f"Teste SSH iniciado: {label} → {device.ip_address}:{device.ssh_port}")
    emit(log, f"Vendor={device.vendor!r} → Netmiko device_type={netmiko_type!r}")
    emit(log, f"Usuário: {device.username!r}")

    try:
        from netmiko import ConnectHandler
        import asyncio

        password = decrypt(device.password_encrypted)

        device_params = {
            "device_type": netmiko_type,
            "host": device.ip_address,
            "port": device.ssh_port,
            "username": device.username,
            "password": password,
            "timeout": 60,
            "auth_timeout": 30,
            "fast_cli": False,
        }

        def _probe_ssh() -> None:
            conn = None
            try:
                conn = ConnectHandler(**device_params)
                emit(log, "SSH autenticado; detectando prompt do equipamento...")
                conn.find_prompt()
                emit(log, f"Prompt OK — host {conn.host!r}, sessão interativa válida")
            finally:
                if conn:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass
                    emit(log, "Sessão SSH encerrada.")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _probe_ssh)

        msg = "Conexão SSH estabelecida e prompt confirmado"
        snmp_info: dict
        vendor_l = (device.vendor or "").strip().lower()

        def _append_inv_summary(body: dict, label: str) -> None:
            nonlocal msg
            _la = body.get("local_as")
            msg += (
                f" | {label}: {body['interface_count']} interfaces, "
                f"{body['bgp_peer_count']} peers BGP, {body['vrf_count']} VRFs, "
                f"AS local {_la if _la is not None else '—'}"
            )

        if vendor_l == "huawei":
            try:
                body = await persist_huawei_cli_inventory(
                    db,
                    device.id,
                    device,
                    log,
                    source="test_connection",
                    intro_message=(
                        "SSH OK — inventário Huawei (comandos display *, VRF BGP — netops/VRP)..."
                    ),
                )
                snmp_info = {"skipped": False, "ok": True, "method": "ssh_huawei", **body}
                _append_inv_summary(body, "Inventário SSH/VRP")
            except Exception as cli_e:
                emit(log, f"Inventário SSH Huawei falhou: {cli_e!s}")
                if device.snmp_community:
                    try:
                        body = await persist_snmp_inventory(
                            db,
                            device.id,
                            device,
                            log,
                            intro_message="Fallback SNMP após falha da coleta SSH Huawei...",
                            source="test_connection",
                        )
                        snmp_info = {"skipped": False, "ok": True, "method": "snmp_fallback", **body}
                        _append_inv_summary(body, "Inventário SNMP (fallback)")
                    except Exception as sn_e:
                        emit(log, f"Fallback SNMP também falhou: {sn_e!s}")
                        snmp_info = {
                            "skipped": False,
                            "ok": False,
                            "method": "failed",
                            "error": f"SSH Huawei: {cli_e}; SNMP: {sn_e}",
                        }
                        msg += " | Inventário falhou (SSH e SNMP)"
                else:
                    snmp_info = {"skipped": False, "ok": False, "method": "failed", "error": str(cli_e)}
                    msg += f" | Inventário falhou: {cli_e}"
        elif device.snmp_community:
            try:
                body = await persist_snmp_inventory(
                    db,
                    device.id,
                    device,
                    log,
                    intro_message=(
                        "SSH OK — inventário SNMP: interfaces, IPs, peering BGP, VRFs..."
                    ),
                    source="test_connection",
                )
                snmp_info = {"skipped": False, "ok": True, "method": "snmp", **body}
                _append_inv_summary(body, "SNMP")
            except Exception as sn_e:
                emit(log, f"Inventário SNMP após SSH falhou: {sn_e!s}")
                snmp_info = {"skipped": False, "ok": False, "method": "snmp", "error": str(sn_e)}
                msg += f" | SNMP falhou: {sn_e}"
        else:
            emit(
                log,
                "Inventário omitido: para Huawei use coleta SSH; demais vendors — cadastre SNMP.",
            )
            snmp_info = {"skipped": True, "ok": None, "method": None}

        return DeviceConnectTest(success=True, message=msg, log=log, snmp=snmp_info)

    except Exception as e:
        emit(log, f"Falha no teste SSH: {e!s}")
        return DeviceConnectTest(success=False, message=str(e), log=log, snmp=None)


# ---------- helpers ----------

async def _get_or_404(device_id: int, db: AsyncSession, user: CurrentUserCtx) -> Device:
    return await get_device_for_user(device_id, db, user)


def _vendor_to_netmiko(vendor: str) -> str:
    mapping = {
        "Huawei": "huawei_vrp",
        "Cisco": "cisco_ios",
        "Juniper": "juniper_junos",
        "Arista": "arista_eos",
        "ZTE": "zte_zxros",
        "MikroTik": "mikrotik_routeros",
    }
    return mapping.get(vendor, "cisco_ios")
