from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Base, get_db
from ..deps.auth import CurrentUserCtx, require_permission
from ..services.system_update_service import system_update_service

router = APIRouter(prefix="/api/management", tags=["management"])


class BackupExportResponse(BaseModel):
    exported_at: str
    table_counts: dict[str, int]
    data: dict[str, list[dict[str, Any]]]


class BackupImportRequest(BaseModel):
    data: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class BackupImportResponse(BaseModel):
    imported_at: str
    table_counts: dict[str, int]


class SystemUpdateStatusResponse(BaseModel):
    current_version: str
    latest_version: str | None = None
    latest_source: str | None = None
    status: str
    update_available: bool
    last_checked_at: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    error: str | None = None
    running: bool
    restart_required: bool
    logs: list[str] = Field(default_factory=list)


def _ensure_superadmin(user: CurrentUserCtx) -> None:
    if not user.is_superadmin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas superadmin pode exportar/importar backup completo.",
        )


def _json_safe(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _coerce_value(v: Any, column) -> Any:
    if v is None:
        return None
    ctype = column.type
    if isinstance(ctype, DateTime):
        if isinstance(v, datetime):
            if getattr(ctype, "timezone", False) and v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        if isinstance(v, str):
            try:
                s = v.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
            except ValueError:
                return v
            if getattr(ctype, "timezone", False) and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    if isinstance(ctype, Boolean):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return bool(v)
    if isinstance(ctype, (Integer, BigInteger)) and not isinstance(v, bool):
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
    return v


def _safe_pg_identifier(name: str) -> str:
    if not name or not name.replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail=f"Identificador de tabela/coluna inválido: {name!r}")
    return name


async def _resync_postgresql_sequences(db: AsyncSession) -> None:
    """Após importar IDs explícitos, alinha sequências SERIAL/IDENTITY (evita colisão no próximo INSERT)."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    for table in Base.metadata.sorted_tables:
        pks = list(table.primary_key.columns)
        if len(pks) != 1:
            continue
        col = pks[0]
        if not isinstance(col.type, (Integer, BigInteger)):
            continue
        tname = _safe_pg_identifier(table.name)
        cname = _safe_pg_identifier(col.name)
        reg = f"public.{tname}"
        seq_row = await db.execute(text("SELECT pg_get_serial_sequence(:reg, :cname)"), {"reg": reg, "cname": cname})
        seq_name = seq_row.scalar()
        if not seq_name:
            continue
        mx_row = await db.execute(text(f'SELECT MAX("{cname}") AS m FROM "{tname}"'))
        mx = mx_row.scalar()
        if mx is None:
            await db.execute(
                text("SELECT setval(CAST(:seq AS regclass), 1, false)"),
                {"seq": seq_name},
            )
        else:
            await db.execute(
                text("SELECT setval(CAST(:seq AS regclass), CAST(:mx AS bigint), true)"),
                {"seq": seq_name, "mx": int(mx)},
            )


@router.get("/backup/export", response_model=BackupExportResponse)
async def export_backup(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ensure_superadmin(user)
    ordered_tables = list(Base.metadata.sorted_tables)
    out: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}

    for table in ordered_tables:
        result = await db.execute(select(table))
        rows = [dict(r) for r in result.mappings().all()]
        rows = [{k: _json_safe(v) for k, v in row.items()} for row in rows]
        out[table.name] = rows
        counts[table.name] = len(rows)

    return BackupExportResponse(
        exported_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        table_counts=counts,
        data=out,
    )


@router.post("/backup/import", response_model=BackupImportResponse)
async def import_backup(
    body: BackupImportRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ensure_superadmin(user)
    payload = body.data or {}
    if not payload:
        raise HTTPException(status_code=422, detail="Payload de backup vazio.")

    table_by_name = {t.name: t for t in Base.metadata.sorted_tables}
    unknown = sorted(k for k in payload.keys() if k not in table_by_name)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Tabelas desconhecidas no backup: {', '.join(unknown)}",
        )

    present_tables = [t for t in Base.metadata.sorted_tables if t.name in payload]
    imported_counts: dict[str, int] = {}

    async with db.begin_nested():
        for table in reversed(present_tables):
            await db.execute(table.delete())

        for table in present_tables:
            rows = payload.get(table.name) or []
            if not isinstance(rows, list):
                raise HTTPException(status_code=422, detail=f"Tabela '{table.name}' deve ser uma lista de linhas.")
            if rows:
                cooked: list[dict[str, Any]] = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise HTTPException(status_code=422, detail=f"Linha inválida em '{table.name}'.")
                    cooked.append(
                        {
                            col.name: _coerce_value(row.get(col.name), col)
                            for col in table.columns
                            if col.name in row
                        }
                    )
                await db.execute(insert(table), cooked)
            imported_counts[table.name] = len(rows)

    await _resync_postgresql_sequences(db)

    return BackupImportResponse(
        imported_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        table_counts=imported_counts,
    )


@router.get("/system-update/status", response_model=SystemUpdateStatusResponse)
async def system_update_status(
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ensure_superadmin(user)
    return SystemUpdateStatusResponse.model_validate(system_update_service.status())


@router.post("/system-update/check", response_model=SystemUpdateStatusResponse)
async def system_update_check(
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ensure_superadmin(user)
    return SystemUpdateStatusResponse.model_validate(system_update_service.check())


@router.post("/system-update/run", response_model=SystemUpdateStatusResponse)
async def system_update_run(
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ensure_superadmin(user)
    try:
        state = system_update_service.start_update(user.username)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return SystemUpdateStatusResponse.model_validate(state)
