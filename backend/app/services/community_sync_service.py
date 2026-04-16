"""
Sincronização Huawei VRP (NE8000): ``ip community-filter`` → biblioteca; ``ip community-list`` → sets.

- Membros do set guardam ``community_value`` e ligam à biblioteca só se existir ``community-filter``
  com o mesmo valor (``missing_in_library`` caso contrário). Não se criam filtros fictícios a partir de listas.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    CommunityLibraryItem,
    CommunitySet,
    CommunitySetMember,
    CommunitySyncAudit,
    Configuration,
    Device,
    DeviceCommunityList,
)
from .huawei_community_parser import (
    LIST_HEADER_EMPTY_VALUE,
    parse_running_config_communities,
)

IMPORTED_COMMUNITY_SET_ORIGINS = frozenset({"discovered", "discovered_running_config", "discovered_live"})
ORIGINS_DISCOVERED_SET = IMPORTED_COMMUNITY_SET_ORIGINS  # compat


def ordered_community_list_groups(parsed) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """Agrupa ``ip community-list NAME`` com (valor, descrição opcional da linha)."""
    order: list[str] = []
    bag: dict[str, list[tuple[str, str | None]]] = {}
    for lst in parsed.community_lists:
        n = lst.list_name
        if n not in bag:
            order.append(n)
            bag[n] = []
        v = (lst.value or "").strip()
        if v and v != LIST_HEADER_EMPTY_VALUE:
            bag[n].append((v, lst.value_description))
    return [(name, bag[name]) for name in order]


def _slugify_for_set_slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()
    return s[:80] or "clist"


def _sanitize_vrp_object_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_") or "CLIST1"
    return n[:63]


def coalesce_groups_by_vrp_object_name(
    groups: list[tuple[str, list[tuple[str, str | None]]]],
) -> list[tuple[str, str, list[tuple[str, str | None]]]]:
    """
    Consolida listas que sanitizam para o mesmo ``vrp_object_name`` para evitar
    ``UNIQUE(device_id, vrp_object_name)`` no insert.
    """
    out: list[tuple[str, str, list[tuple[str, str | None]]]] = []
    idx_by_vn: dict[str, int] = {}
    seen_by_vn: dict[str, set[str]] = {}
    for list_name, members in groups:
        vn = _sanitize_vrp_object_name(list_name)
        if vn not in idx_by_vn:
            idx_by_vn[vn] = len(out)
            seen_by_vn[vn] = set()
            out.append((list_name, vn, []))
        i = idx_by_vn[vn]
        _, _, bucket = out[i]
        seen = seen_by_vn[vn]
        for val, desc in members:
            vv = (val or "").strip()
            if not vv:
                continue
            k = vv.lower()
            if k in seen:
                continue
            seen.add(k)
            bucket.append((vv, (desc or "").strip() or None))
    return out


async def _unique_community_set_slug(db: AsyncSession, device_id: int, base: str) -> str:
    b = (base or "set")[:100]
    cand = b
    n = 2
    while True:
        r = await db.execute(
            select(CommunitySet.id).where(CommunitySet.device_id == device_id, CommunitySet.slug == cand)
        )
        if r.scalar_one_or_none() is None:
            return cand
        cand = f"{b}-{n}"
        n += 1


async def latest_running_config_text(db: AsyncSession, device_id: int) -> str | None:
    r = await db.execute(
        select(Configuration.config_text)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    row = r.scalar_one_or_none()
    if not row:
        return None
    t = (row or "").strip()
    return t or None


async def resolve_library_link_for_value(
    db: AsyncSession,
    *,
    device_id: int,
    community_value: str,
) -> tuple[CommunityLibraryItem | None, bool]:
    """Devolve (linha da biblioteca, missing) — só ``basic``/``advanced`` ativos."""
    val = (community_value or "").strip()
    if not val:
        return None, True
    r = await db.execute(
        select(CommunityLibraryItem).where(
            CommunityLibraryItem.device_id == device_id,
            CommunityLibraryItem.community_value == val,
            CommunityLibraryItem.match_type.in_(("basic", "advanced")),
            CommunityLibraryItem.is_active.is_(True),
        )
    )
    rows = list(r.scalars().all())
    if not rows:
        return None, True
    return rows[0], False


async def _deactivate_wrong_library_rows_from_lists(
    db: AsyncSession,
    *,
    device_id: int,
    groups: list[tuple[str, list[tuple[str, str | None]]]],
    filter_names: set[str],
) -> int:
    """
    Inativa linhas na biblioteca que são valores de ``ip community-list`` gravados com o nome da lista
    como se fossem ``filter_name``, quando esse nome **não** é também um ``community-filter``.
    """
    changed = 0
    for list_name, members in groups:
        ln = (list_name or "").strip()
        if not ln or ln in filter_names:
            continue
        vals = {t[0].strip() for t in members if t[0] and t[0].strip()}
        if not vals:
            continue
        r = await db.execute(
            select(CommunityLibraryItem).where(
                CommunityLibraryItem.device_id == device_id,
                CommunityLibraryItem.filter_name == ln,
                CommunityLibraryItem.community_value.in_(vals),
                CommunityLibraryItem.is_active.is_(True),
            )
        )
        for row in r.scalars().all():
            row.is_active = False
            changed += 1
    return changed


async def _record_sync_audit(
    db: AsyncSession,
    *,
    device_id: int,
    user_id: int | None,
    source: str,
    action: str,
    details: dict[str, Any],
    status: str,
) -> None:
    db.add(
        CommunitySyncAudit(
            device_id=device_id,
            user_id=user_id,
            source=source,
            action=action,
            details_json=details,
            status=status,
        )
    )


async def sync_communities_from_config_text(
    db: AsyncSession,
    *,
    device: Device,
    config_text: str,
    user_id: int | None,
    sync_source: str,
) -> dict[str, int]:
    parsed = parse_running_config_communities(config_text)
    company_id = int(device.company_id)
    lib_origin = "discovered_running_config" if sync_source == "running_config" else "discovered_live"
    set_origin = lib_origin
    inserted = updated = skipped_manual = 0
    wrong_library_deactivated = 0
    members_missing = 0

    await db.execute(
        delete(CommunityLibraryItem).where(
            CommunityLibraryItem.device_id == device.id,
            CommunityLibraryItem.match_type == "legacy",
            CommunityLibraryItem.origin.in_(("discovered", "discovered_running_config", "discovered_live")),
        )
    )
    await db.execute(delete(DeviceCommunityList).where(DeviceCommunityList.device_id == device.id))

    await db.execute(
        delete(CommunitySet).where(
            CommunitySet.device_id == device.id,
            CommunitySet.origin.in_(tuple(IMPORTED_COMMUNITY_SET_ORIGINS)),
        )
    )

    filter_names = {f.name.strip() for f in parsed.community_filters if f.name}
    groups = ordered_community_list_groups(parsed)
    merged_groups = coalesce_groups_by_vrp_object_name(groups)
    wrong_library_deactivated = await _deactivate_wrong_library_rows_from_lists(
        db, device_id=device.id, groups=groups, filter_names=filter_names
    )

    async def _upsert_filter(
        *,
        filter_name: str,
        community_value: str,
        match_type: str,
        action: str,
        index_order: int | None,
        description: str | None = None,
    ) -> None:
        nonlocal inserted, updated, skipped_manual
        r = await db.execute(
            select(CommunityLibraryItem).where(
                CommunityLibraryItem.device_id == device.id,
                CommunityLibraryItem.filter_name == filter_name,
                CommunityLibraryItem.community_value == community_value,
                CommunityLibraryItem.match_type == match_type,
            )
        )
        row = r.scalar_one_or_none()
        if row is None:
            db.add(
                CommunityLibraryItem(
                    device_id=device.id,
                    company_id=company_id,
                    filter_name=filter_name,
                    community_value=community_value,
                    match_type=match_type,
                    action=action,
                    index_order=index_order,
                    origin=lib_origin,
                    description=description,
                    is_active=True,
                )
            )
            inserted += 1
            return
        if (row.origin or "") == "manual":
            skipped_manual += 1
            return
        row.action = action
        row.index_order = index_order
        row.company_id = company_id
        row.origin = lib_origin
        row.is_active = True
        if description is not None:
            row.description = description
        updated += 1

    for f in parsed.community_filters:
        await _upsert_filter(
            filter_name=f.name,
            community_value=f.value,
            match_type=f.match_type,
            action=f.action,
            index_order=f.index,
            description=f"Entrada ip community-filter {f.match_type} «{f.name}» (running-config).",
        )

    skipped_conf = 0
    synced = 0
    for list_name, vn, comms in merged_groups:
        r_man = await db.execute(
            select(CommunitySet.id).where(
                CommunitySet.device_id == device.id,
                CommunitySet.vrp_object_name == vn,
                CommunitySet.origin == "app_created",
            )
        )
        if r_man.scalar_one_or_none() is not None:
            skipped_conf += 1
            continue
        slug = await _unique_community_set_slug(db, device.id, _slugify_for_set_slug(f"d-{list_name}"))
        display_name = (list_name or vn)[:200]
        s = CommunitySet(
            device_id=device.id,
            company_id=company_id,
            name=display_name,
            slug=slug,
            vrp_object_name=vn,
            origin=set_origin,
            discovered_members_json=None,
            description="Importado: ip community-list (VRP). Membros validados contra community-filter na biblioteca.",
            status="imported",
            is_active=True,
            created_by=None,
            updated_by=None,
        )
        db.add(s)
        await db.flush()
        pos = 0
        for val, line_desc in comms:
            vv = (val or "").strip()
            if not vv:
                continue
            li, missing = await resolve_library_link_for_value(db, device_id=device.id, community_value=vv)
            if missing:
                members_missing += 1
            db.add(
                CommunitySetMember(
                    community_set_id=s.id,
                    community_value=vv,
                    linked_library_item_id=li.id if li else None,
                    missing_in_library=missing,
                    value_description=(line_desc or "").strip() or None,
                    position=pos,
                )
            )
            pos += 1
        synced += 1

    details = {
        "sync_source": sync_source,
        "community_filter_rows": len(parsed.community_filters),
        "ip_community_list_rows": len(groups),
        "ip_community_list_rows_merged_by_vrp_name": len(merged_groups),
        "library_inserted": inserted,
        "library_updated": updated,
        "library_skipped_manual": skipped_manual,
        "wrong_library_rows_deactivated": wrong_library_deactivated,
        "set_members_missing_library": members_missing,
        "discovered_sets_synced": synced,
        "skipped_discovered_vrp_conflicts": skipped_conf,
    }
    await _record_sync_audit(
        db,
        device_id=device.id,
        user_id=user_id,
        source=sync_source,
        action="import",
        details=details,
        status="success",
    )

    await db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped_no_config": 0,
        "skipped_manual": skipped_manual,
        "community_filter_rows": len(parsed.community_filters),
        "ip_community_list_rows": len(groups),
        "discovered_sets_synced": synced,
        "skipped_discovered_vrp_conflicts": skipped_conf,
        "wrong_library_rows_deactivated": wrong_library_deactivated,
        "set_members_missing_library": members_missing,
    }


async def resync_from_saved_configuration(
    db: AsyncSession,
    *,
    device: Device,
    user_id: int | None = None,
) -> dict[str, int]:
    cfg = await latest_running_config_text(db, device.id)
    if not cfg:
        return {
            "inserted": 0,
            "updated": 0,
            "skipped_no_config": 1,
            "skipped_manual": 0,
            "community_filter_rows": 0,
            "ip_community_list_rows": 0,
            "discovered_sets_synced": 0,
            "skipped_discovered_vrp_conflicts": 0,
            "wrong_library_rows_deactivated": 0,
            "set_members_missing_library": 0,
        }
    return await sync_communities_from_config_text(
        db,
        device=device,
        config_text=cfg,
        user_id=user_id,
        sync_source="running_config",
    )


async def resync_from_live_device(
    db: AsyncSession,
    *,
    device: Device,
    user_id: int | None = None,
) -> dict[str, int]:
    from .community_live_config import fetch_current_configuration_via_ssh

    cfg = await fetch_current_configuration_via_ssh(device)
    if not (cfg or "").strip():
        await _record_sync_audit(
            db,
            device_id=device.id,
            user_id=user_id,
            source="live_device",
            action="import",
            details={"error": "empty_or_unreachable"},
            status="failed",
        )
        await db.commit()
        return {
            "inserted": 0,
            "updated": 0,
            "skipped_no_config": 1,
            "skipped_manual": 0,
            "community_filter_rows": 0,
            "ip_community_list_rows": 0,
            "discovered_sets_synced": 0,
            "skipped_discovered_vrp_conflicts": 0,
            "wrong_library_rows_deactivated": 0,
            "set_members_missing_library": 0,
        }
    return await sync_communities_from_config_text(
        db,
        device=device,
        config_text=cfg,
        user_id=user_id,
        sync_source="live_device",
    )
