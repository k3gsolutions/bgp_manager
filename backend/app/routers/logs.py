from fastapi import APIRouter, Depends, Query

from ..activity_log import get_recent_events
from ..deps.auth import CurrentUserCtx, require_permission

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/recent")
async def recent_logs(
    limit: int = Query(100, ge=1, le=1000, description="Número máximo de eventos recentes"),
    _: CurrentUserCtx = require_permission("logs.view"),
):
    return get_recent_events(limit=limit)
