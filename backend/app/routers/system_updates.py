from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps.auth import CurrentUserCtx, require_permission
from ..models import SystemUpdateHistory
from ..services import system_update_remote_service

router = APIRouter(prefix="/api/system", tags=["system"])


class SystemVersionOut(BaseModel):
    current_version: str
    app_env: str


class SystemUpdateStatusOut(BaseModel):
    running: bool
    status: str
    latest_in_progress_id: int | None = None


class SystemCheckUpdateOut(BaseModel):
    update_available: bool
    update_type: Literal["patch", "minor", "major", "none"]
    current_version: str
    latest_version: str | None = None
    latest_release_notes_summary: str | None = None
    latest_tag_source: str | None = None
    last_check_id: int | None = None
    status: str
    last_checked_at: str | None = None


class SystemApplyUpdateRequest(BaseModel):
    mode: Literal["manual", "auto_patch"] = "manual"
    confirm: bool = False
    confirm_strong: bool = False
    # Opcional: se informado, bloqueia caso o GitHub retorne outro `latest_version`.
    target_version: str | None = None


class SystemApplyUpdateOut(BaseModel):
    history_id: int
    status: str


class SystemRollbackUpdateRequest(BaseModel):
    confirm: bool = False
    confirm_strong: bool = False
    # Opcional: se informado, usa esse histórico como referência.
    history_id: int | None = None


class SystemRollbackUpdateOut(BaseModel):
    history_id: int
    status: str


class SystemUpdateHistoryOut(BaseModel):
    id: int
    from_version: str
    to_version: str
    update_type: str
    triggered_by: int | None
    mode: str
    status: str
    log_text: str
    created_at: str
    finished_at: str | None


@router.get("/version", response_model=SystemVersionOut)
async def system_version(
    user: CurrentUserCtx = require_permission("management.backup"),
):
    # Acesso protegido: evita leaking do ambiente/versões para usuários sem permissão.
    _ = user
    v = system_update_remote_service.get_local_version()
    return SystemVersionOut(current_version=v, app_env=(system_update_remote_service.app_env or ""))


@router.get("/update-status", response_model=SystemUpdateStatusOut)
async def update_status(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    _ = user
    running_q = await db.execute(
        select(SystemUpdateHistory)
        .where(SystemUpdateHistory.status == "in_progress")
        .order_by(SystemUpdateHistory.created_at.desc())
        .limit(1)
    )
    row = running_q.scalar_one_or_none()
    if row is None:
        return SystemUpdateStatusOut(running=False, status="idle", latest_in_progress_id=None)
    return SystemUpdateStatusOut(running=True, status="running", latest_in_progress_id=row.id)


@router.post("/check-update", response_model=SystemCheckUpdateOut)
async def check_update(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    latest = await system_update_remote_service.check_update(db=db, user_id=user.id)
    return SystemCheckUpdateOut(**latest)


@router.post("/apply-update", response_model=SystemApplyUpdateOut)
async def apply_update(
    body: SystemApplyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    try:
        res = await system_update_remote_service.apply_update(
            db=db,
            user_id=user.id,
            mode=body.mode,
            confirm=body.confirm,
            confirm_strong=body.confirm_strong,
            target_version=body.target_version,
        )
    except HTTPException as e:
        raise e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e
    return SystemApplyUpdateOut(history_id=res["history_id"], status=res["status"])


@router.post("/rollback-update", response_model=SystemRollbackUpdateOut)
async def rollback_update(
    body: SystemRollbackUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
):
    res = await system_update_remote_service.rollback_update(
        db=db,
        user_id=user.id,
        confirm=body.confirm,
        confirm_strong=body.confirm_strong,
        history_id=body.history_id,
    )
    return SystemRollbackUpdateOut(history_id=res["history_id"], status=res["status"])


@router.get("/update-history", response_model=list[SystemUpdateHistoryOut])
async def update_history(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("management.backup"),
    limit: int = Query(default=20, ge=1, le=200),
):
    _ = user
    q = await db.execute(
        select(SystemUpdateHistory)
        .order_by(SystemUpdateHistory.created_at.desc())
        .limit(limit)
    )
    rows = q.scalars().all()
    out: list[SystemUpdateHistoryOut] = []
    for r in rows:
        out.append(
            SystemUpdateHistoryOut(
                id=r.id,
                from_version=r.from_version,
                to_version=r.to_version,
                update_type=r.update_type,
                triggered_by=r.triggered_by,
                mode=r.mode,
                status=r.status,
                log_text=r.log_text or "",
                created_at=r.created_at.isoformat(),
                finished_at=r.finished_at.isoformat() if r.finished_at else None,
            )
        )
    return out

