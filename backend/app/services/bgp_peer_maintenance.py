"""Operações de manutenção em peers BGP persistidos."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BGPPeer


async def purge_inactive_bgp_peers(db: AsyncSession, device_id: int) -> int:
    """Remove linhas `bgp_peers` com `is_active=False` para o equipamento."""
    res = await db.execute(
        delete(BGPPeer).where(BGPPeer.device_id == device_id, BGPPeer.is_active.is_(False))
    )
    await db.flush()
    return int(res.rowcount or 0)
