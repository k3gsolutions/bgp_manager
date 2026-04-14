import json
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
from ..models import BGPPeer, Company, Device, Interface, PrefixLookupHistory
from ..schemas import (
    BgpExportLookupRequest,
    BgpExportLookupResponse,
    DeviceBatchImportFailure,
    DeviceBatchImportRequest,
    DeviceBatchImportResponse,
    DeviceConnectTest,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from ..services.bgp_export_lookup import run_huawei_bgp_export_lookup
from ..services.bgp_peer_resolve import build_peer_hints_from_db
from ..services.bgp_peer_maintenance import purge_inactive_bgp_peers
from ..services.huawei_ssh_inventory import persist_huawei_cli_inventory
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
