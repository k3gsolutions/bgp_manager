"""Compat: re-exporta agrupamento de listas e delega resync ao ``community_sync_service``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Device
from .community_sync_service import (
    coalesce_groups_by_vrp_object_name,
    latest_running_config_text,
    ordered_community_list_groups,
    resync_from_saved_configuration,
)


async def resync_library_from_config(
    db: AsyncSession,
    *,
    device: Device,
    user_id: int | None = None,
) -> dict[str, int]:
    return await resync_from_saved_configuration(db, device=device, user_id=user_id)


__all__ = [
    "coalesce_groups_by_vrp_object_name",
    "latest_running_config_text",
    "ordered_community_list_groups",
    "resync_library_from_config",
]
