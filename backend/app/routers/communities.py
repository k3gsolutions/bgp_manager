"""API — biblioteca e community sets (Huawei VRP, fase 1)."""

from __future__ import annotations

from sqlalchemy import case, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from ..database import get_db
from ..deps.auth import CurrentUserCtx, get_device_for_user, require_permission
from ..models import (
    CommunityChangeAudit,
    CommunityLibraryItem,
    CommunitySet,
    CommunitySetMember,
    Configuration,
)
from ..schemas_communities import (
    CommunityApplyRequest,
    CommunityApplyResultOut,
    CommunityLibraryItemOut,
    CommunityPreviewOut,
    CommunityResyncResult,
    CommunitySetCloneIn,
    CommunitySetCompareIn,
    CommunitySetCompareOut,
    CommunitySetCreate,
    CommunitySetMemberOut,
    CommunitySetOut,
    CommunitySetUpdate,
    CommunitySetUsageOut,
)
from ..services.community_apply_service import (
    apply_community_set,
    build_candidate_config_text,
    build_preview,
    record_audit,
    slugify_display_name,
    validate_vrp_object_name,
)
from ..services.community_sync_service import (
    IMPORTED_COMMUNITY_SET_ORIGINS,
    resync_from_live_device,
    resync_from_saved_configuration,
    resolve_library_link_for_value,
)
from ..services.huawei_community_parser import (
    community_list_names_in_config,
    format_phase1_community_list_block,
    parse_running_config_communities,
    usage_counts_for_library_names,
)

router = APIRouter(prefix="/api/devices", tags=["communities"])


async def _usage_counts(db: AsyncSession, device_id: int) -> dict[str, int]:
    r = await db.execute(
        select(Configuration.config_text)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    txt = r.scalar_one_or_none() or ""
    if not (txt or "").strip():
        return {}
    parsed = parse_running_config_communities(txt)
    return usage_counts_for_library_names(parsed)


def _set_to_out(s: CommunitySet) -> CommunitySetOut:
    origin = (getattr(s, "origin", None) or "app_created") or "app_created"
    disc_members: list[str] = []
    implied_preview: str | None = None
    members_out: list[CommunitySetMemberOut] = []
    for m in sorted(s.members, key=lambda x: x.position):
        li = m.linked_library_item
        members_out.append(
            CommunitySetMemberOut(
                id=m.id,
                position=m.position,
                community_value=(m.community_value or "").strip(),
                linked_library_item_id=m.linked_library_item_id,
                missing_in_library=bool(m.missing_in_library),
                linked_filter_name=(li.filter_name if li else "") or "",
                value_description=m.value_description,
            )
        )
    if origin in IMPORTED_COMMUNITY_SET_ORIGINS and not members_out:
        disc_members = [str(x).strip() for x in (s.discovered_members_json or []) if str(x).strip()]
        implied_preview = format_phase1_community_list_block(s.vrp_object_name, disc_members).rstrip("\n")
        members_out = [
            CommunitySetMemberOut(
                id=None,
                position=i,
                community_value=v,
                linked_library_item_id=None,
                missing_in_library=True,
                linked_filter_name="",
                value_description=None,
            )
            for i, v in enumerate(disc_members)
        ]
    elif members_out:
        vals = [m.community_value for m in members_out if (m.community_value or "").strip()]
        implied_preview = format_phase1_community_list_block(s.vrp_object_name, vals).rstrip("\n") if vals else None
    total = len(members_out)
    miss = sum(1 for x in members_out if x.missing_in_library)
    return CommunitySetOut(
        id=s.id,
        device_id=s.device_id,
        company_id=s.company_id,
        name=s.name,
        slug=s.slug,
        vrp_object_name=s.vrp_object_name,
        origin=origin,
        discovered_members=disc_members,
        implied_config_preview=implied_preview,
        description=s.description,
        status=s.status,
        created_by=s.created_by,
        updated_by=s.updated_by,
        created_at=s.created_at,
        updated_at=s.updated_at,
        members=members_out,
        members_total=total,
        members_resolved=total - miss,
        members_missing=miss,
    )


def _community_values_as_set(s: CommunitySet) -> set[str]:
    o = (getattr(s, "origin", None) or "app_created") or "app_created"
    if o in IMPORTED_COMMUNITY_SET_ORIGINS and not s.members:
        return {str(x).strip() for x in (s.discovered_members_json or []) if str(x).strip()}
    out: set[str] = set()
    for m in sorted(s.members, key=lambda x: x.position):
        v = (m.community_value or "").strip()
        if v:
            out.add(v)
    return out


async def _unique_slug(db: AsyncSession, device_id: int, base: str) -> str:
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


@router.get("/{device_id}/communities/library", response_model=list[CommunityLibraryItemOut])
async def list_community_library(
    device_id: int,
    q: str | None = Query(None, description="Busca por nome ou valor (contains)"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.view"),
):
    device = await get_device_for_user(device_id, db, user)
    usage = await _usage_counts(db, device_id)
    stmt = (
        select(CommunityLibraryItem)
        .where(
            CommunityLibraryItem.device_id == device.id,
            CommunityLibraryItem.match_type.in_(("basic", "advanced")),
            CommunityLibraryItem.is_active.is_(True),
        )
        .order_by(
            CommunityLibraryItem.filter_name,
            CommunityLibraryItem.match_type,
            CommunityLibraryItem.index_order,
        )
    )
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CommunityLibraryItem.filter_name.ilike(needle),
                CommunityLibraryItem.community_value.ilike(needle),
                CommunityLibraryItem.description.ilike(needle),
            )
        )
    r = await db.execute(stmt)
    rows = [it for it in r.scalars().all() if int(it.device_id) == int(device.id)]
    return [
        CommunityLibraryItemOut(
            id=it.id,
            device_id=it.device_id,
            company_id=it.company_id,
            filter_name=it.filter_name,
            community_value=it.community_value,
            match_type=it.match_type,
            action=it.action,
            index_order=it.index_order,
            origin=it.origin,
            description=it.description,
            tags_json=it.tags_json,
            is_system=it.is_system,
            is_active=bool(getattr(it, "is_active", True)),
            created_at=it.created_at,
            updated_at=it.updated_at,
            usage_count=int(usage.get(it.filter_name, 0)),
        )
        for it in rows
    ]


@router.post("/{device_id}/communities/resync-from-config", response_model=CommunityResyncResult)
async def resync_communities_from_config(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
):
    device = await get_device_for_user(device_id, db, user)
    stats = await resync_from_saved_configuration(db, device=device, user_id=user.id)
    return CommunityResyncResult(**stats)


@router.post("/{device_id}/communities/resync-live", response_model=CommunityResyncResult)
async def resync_communities_live(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
):
    device = await get_device_for_user(device_id, db, user)
    try:
        stats = await resync_from_live_device(db, device=device, user_id=user.id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha ao obter running-config por SSH: {e!s}",
        ) from e
    return CommunityResyncResult(**stats)


@router.get("/{device_id}/community-sets", response_model=list[CommunitySetOut])
async def list_community_sets(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.view"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.device_id == device.id)
        .order_by(
            case((CommunitySet.origin.in_(tuple(IMPORTED_COMMUNITY_SET_ORIGINS)), 0), else_=1),
            CommunitySet.name.asc(),
        )
    )
    sets = [s for s in r.scalars().unique().all() if int(s.device_id) == int(device.id)]
    return [_set_to_out(s) for s in sets]


@router.post("/{device_id}/community-sets", response_model=CommunitySetOut, status_code=status.HTTP_201_CREATED)
async def create_community_set(
    device_id: int,
    payload: CommunitySetCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
):
    device = await get_device_for_user(device_id, db, user)

    ids = list(dict.fromkeys(payload.member_library_item_ids))
    if len(ids) != len(payload.member_library_item_ids):
        raise HTTPException(status_code=400, detail="IDs de biblioteca duplicados no pedido.")

    if ids:
        r = await db.execute(
            select(CommunityLibraryItem).where(
                CommunityLibraryItem.device_id == device.id,
                CommunityLibraryItem.id.in_(ids),
            )
        )
        found = {x.id: x for x in r.scalars().all()}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"Itens de biblioteca inválidos ou de outro dispositivo: {missing}")
        bad = [x.id for x in found.values() if (x.match_type or "") not in ("basic", "advanced")]
        if bad:
            raise HTTPException(
                status_code=400,
                detail="Apenas entradas ``ip community-filter`` (basic/advanced) na biblioteca podem integrar um set.",
            )

    slug_base = slugify_display_name(payload.slug or payload.name)
    slug = await _unique_slug(db, device.id, slug_base)
    vrp_name = (payload.vrp_object_name or "").strip()
    if not vrp_name:
        vrp_name = slug.replace("-", "_")[:63] or "CLIST1"
    try:
        vrp_name = validate_vrp_object_name(vrp_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    dup = await db.execute(
        select(CommunitySet.id).where(
            CommunitySet.device_id == device.id,
            CommunitySet.vrp_object_name == vrp_name,
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Já existe um community set com este vrp_object_name neste dispositivo.")

    s = CommunitySet(
        device_id=device.id,
        company_id=device.company_id,
        name=payload.name.strip(),
        slug=slug,
        vrp_object_name=vrp_name,
        origin="app_created",
        discovered_members_json=None,
        description=(payload.description or "").strip() or None,
        status="draft",
        is_active=True,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(s)
    await db.flush()

    for pos, lid in enumerate(ids):
        li = found[lid]
        db.add(
            CommunitySetMember(
                community_set_id=s.id,
                community_value=(li.community_value or "").strip(),
                linked_library_item_id=li.id,
                missing_in_library=False,
                value_description=None,
                position=pos,
            )
        )
    await db.commit()
    await db.refresh(s)
    r2 = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == s.id)
    )
    s2 = r2.scalar_one()
    return _set_to_out(s2)


@router.post("/{device_id}/community-sets/compare", response_model=CommunitySetCompareOut)
async def compare_community_sets(
    device_id: int,
    payload: CommunitySetCompareIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.view"),
):
    device = await get_device_for_user(device_id, db, user)
    if payload.set_id_a == payload.set_id_b:
        raise HTTPException(status_code=400, detail="Escolha dois sets distintos.")

    async def _load(sid: int) -> CommunitySet | None:
        r = await db.execute(
            select(CommunitySet)
            .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
            .where(CommunitySet.id == sid, CommunitySet.device_id == device.id)
        )
        return r.scalar_one_or_none()

    sa = await _load(payload.set_id_a)
    sb = await _load(payload.set_id_b)
    if sa is None or sb is None:
        raise HTTPException(status_code=404, detail="Um ou ambos os community sets não foram encontrados.")

    va = _community_values_as_set(sa)
    vb = _community_values_as_set(sb)
    only_a = sorted(va - vb, key=str.lower)
    only_b = sorted(vb - va, key=str.lower)
    both = sorted(va & vb, key=str.lower)
    return CommunitySetCompareOut(
        set_a_id=sa.id,
        set_b_id=sb.id,
        set_a_name=sa.name,
        set_b_name=sb.name,
        set_a_origin=(getattr(sa, "origin", None) or "app_created") or "app_created",
        set_b_origin=(getattr(sb, "origin", None) or "app_created") or "app_created",
        members_a_sorted=sorted(va, key=str.lower),
        members_b_sorted=sorted(vb, key=str.lower),
        only_in_a=only_a,
        only_in_b=only_b,
        in_both=both,
    )


@router.post(
    "/{device_id}/community-sets/{set_id}/clone",
    response_model=CommunitySetOut,
    status_code=status.HTTP_201_CREATED,
)
async def clone_community_set(
    device_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
    payload: CommunitySetCloneIn = Body(default_factory=CommunitySetCloneIn),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == set_id, CommunitySet.device_id == device.id)
    )
    src = r.scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")

    base_name = (payload.name or "").strip() or f"Cópia de {src.name}"
    base_name = base_name[:200]

    slug = await _unique_slug(db, device.id, slugify_display_name(base_name))

    vrp_base = (src.vrp_object_name or "set")[:45]
    vrp_name = ""
    for n in range(0, 80):
        cand = f"{vrp_base}_copy"[:63] if n == 0 else f"{vrp_base}_c{n}"[:63]
        try:
            vrp_try = validate_vrp_object_name(cand)
        except ValueError:
            vrp_try = validate_vrp_object_name(f"clone{n}"[:63])
        dup = await db.execute(
            select(CommunitySet.id).where(CommunitySet.device_id == device.id, CommunitySet.vrp_object_name == vrp_try)
        )
        if dup.scalar_one_or_none() is None:
            vrp_name = vrp_try
            break
    if not vrp_name:
        raise HTTPException(status_code=500, detail="Não foi possível gerar um vrp_object_name único para o clone.")

    s = CommunitySet(
        device_id=device.id,
        company_id=device.company_id,
        name=base_name,
        slug=slug,
        vrp_object_name=vrp_name,
        origin="app_created",
        discovered_members_json=None,
        description=(src.description or "").strip() or None,
        status="draft",
        is_active=True,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(s)
    await db.flush()
    if src.members:
        for pos, m in enumerate(sorted(src.members, key=lambda x: x.position)):
            vv = (m.community_value or "").strip()
            if not vv:
                continue
            li, miss = await resolve_library_link_for_value(db, device_id=device.id, community_value=vv)
            db.add(
                CommunitySetMember(
                    community_set_id=s.id,
                    community_value=vv,
                    linked_library_item_id=li.id if li else None,
                    missing_in_library=miss,
                    value_description=m.value_description,
                    position=pos,
                )
            )
    else:
        values: list[str] = []
        if (getattr(src, "origin", None) or "") in IMPORTED_COMMUNITY_SET_ORIGINS:
            values = [str(x).strip() for x in (src.discovered_members_json or []) if str(x).strip()]
        seen: set[str] = set()
        ordered_unique: list[str] = []
        for v in values:
            k = v.lower()
            if k in seen:
                continue
            seen.add(k)
            ordered_unique.append(v)
        for pos, val in enumerate(ordered_unique):
            li, miss = await resolve_library_link_for_value(db, device_id=device.id, community_value=val)
            db.add(
                CommunitySetMember(
                    community_set_id=s.id,
                    community_value=val,
                    linked_library_item_id=li.id if li else None,
                    missing_in_library=miss,
                    value_description=None,
                    position=pos,
                )
            )
    await db.commit()
    r2 = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == s.id)
    )
    return _set_to_out(r2.scalar_one())


@router.get("/{device_id}/community-sets/{set_id}", response_model=CommunitySetOut)
async def get_community_set(
    device_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.view"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == set_id, CommunitySet.device_id == device.id)
    )
    s = r.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")
    return _set_to_out(s)


@router.put("/{device_id}/community-sets/{set_id}", response_model=CommunitySetOut)
async def update_community_set(
    device_id: int,
    set_id: int,
    payload: CommunitySetUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == set_id, CommunitySet.device_id == device.id)
    )
    s = r.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")
    if (getattr(s, "origin", None) or "app_created") in IMPORTED_COMMUNITY_SET_ORIGINS:
        raise HTTPException(
            status_code=400,
            detail="Sets importados do equipamento não podem ser editados. Use clone para obter um rascunho editável.",
        )
    if s.status not in ("draft", "failed", "pending_confirmation"):
        raise HTTPException(
            status_code=400,
            detail="Só é possível editar sets em rascunho, aguardando confirmação ou falha.",
        )

    if payload.name is not None:
        s.name = payload.name.strip()
    if payload.description is not None:
        s.description = payload.description.strip() or None
    if payload.slug is not None:
        new_slug = await _unique_slug(db, device.id, slugify_display_name(payload.slug))
        s.slug = new_slug
    if payload.vrp_object_name is not None:
        try:
            vn = validate_vrp_object_name(payload.vrp_object_name.strip())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        dup = await db.execute(
            select(CommunitySet.id).where(
                CommunitySet.device_id == device.id,
                CommunitySet.vrp_object_name == vn,
                CommunitySet.id != s.id,
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="vrp_object_name já usado noutro set.")
        s.vrp_object_name = vn

    if payload.member_library_item_ids is not None:
        ids = list(dict.fromkeys(payload.member_library_item_ids))
        if ids:
            rli = await db.execute(
                select(CommunityLibraryItem).where(
                    CommunityLibraryItem.device_id == device.id,
                    CommunityLibraryItem.id.in_(ids),
                )
            )
            found_rows = {x.id: x for x in rli.scalars().all()}
            missing = [i for i in ids if i not in found_rows]
            if missing:
                raise HTTPException(status_code=400, detail=f"Itens inválidos: {missing}")
            bad = [x.id for x in found_rows.values() if (x.match_type or "") not in ("basic", "advanced")]
            if bad:
                raise HTTPException(
                    status_code=400,
                    detail="Apenas entradas ``ip community-filter`` (basic/advanced) podem integrar um set.",
                )
        await db.execute(delete(CommunitySetMember).where(CommunitySetMember.community_set_id == s.id))
        for pos, lid in enumerate(ids):
            li = found_rows[lid]
            db.add(
                CommunitySetMember(
                    community_set_id=s.id,
                    community_value=(li.community_value or "").strip(),
                    linked_library_item_id=li.id,
                    missing_in_library=False,
                    value_description=None,
                    position=pos,
                )
            )

    s.updated_by = user.id
    s.status = "draft"
    await db.commit()
    await db.refresh(s)
    r2 = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == s.id)
    )
    return _set_to_out(r2.scalar_one())


@router.delete("/{device_id}/community-sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_community_set(
    device_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.edit"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(select(CommunitySet).where(CommunitySet.id == set_id, CommunitySet.device_id == device.id))
    s = r.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")
    if (getattr(s, "origin", None) or "app_created") in IMPORTED_COMMUNITY_SET_ORIGINS:
        raise HTTPException(
            status_code=400,
            detail="Sets importados são atualizados apenas na sincronização (running-config ou live).",
        )
    if s.status not in ("draft", "failed", "pending_confirmation"):
        raise HTTPException(
            status_code=400,
            detail="Só é possível apagar sets em rascunho, aguardando confirmação ou falha.",
        )
    await record_audit(
        db,
        device_id=device.id,
        community_set_id=s.id,
        user_id=user.id,
        action="delete",
        candidate_config_text="",
        command_sent_text=None,
        device_response_text=None,
        status="success",
    )
    await db.delete(s)
    await db.commit()
    return None


@router.post("/{device_id}/community-sets/{set_id}/preview", response_model=CommunityPreviewOut)
async def preview_community_set(
    device_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.preview"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item))
        .where(CommunitySet.id == set_id, CommunitySet.device_id == device.id)
    )
    s = r.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")
    if (getattr(s, "origin", None) or "app_created") in IMPORTED_COMMUNITY_SET_ORIGINS:
        raise HTTPException(
            status_code=400,
            detail="Sets importados do equipamento não têm preview/apply na app (só visualização e clone).",
        )

    data = await build_preview(db, device=device, community_set=s)
    await record_audit(
        db,
        device_id=device.id,
        community_set_id=s.id,
        user_id=user.id,
        action="preview",
        candidate_config_text=data["candidate_config_text"],
        command_sent_text=None,
        device_response_text=None,
        status="success",
    )
    s.status = "pending_confirmation"
    await db.commit()
    return CommunityPreviewOut(**data)


@router.post("/{device_id}/community-sets/{set_id}/apply", response_model=CommunityApplyResultOut)
async def apply_community_set_endpoint(
    device_id: int,
    set_id: int,
    payload: CommunityApplyRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.apply"),
):
    device = await get_device_for_user(device_id, db, user)
    try:
        out = await apply_community_set(
            db,
            device=device,
            community_set_id=set_id,
            user_id=user.id,
            confirm=payload.confirm,
            expected_candidate_sha256=payload.expected_candidate_sha256,
            acknowledge_missing_library_refs=payload.acknowledge_missing_library_refs,
        )
        return CommunityApplyResultOut(**out)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao aplicar no dispositivo: {e!s}") from e


@router.get("/{device_id}/community-sets/{set_id}/usage", response_model=CommunitySetUsageOut)
async def community_set_usage(
    device_id: int,
    set_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("communities.view"),
):
    device = await get_device_for_user(device_id, db, user)
    r = await db.execute(
        select(CommunitySet)
        .options(selectinload(CommunitySet.members))
        .where(CommunitySet.id == set_id, CommunitySet.device_id == device.id)
    )
    s = r.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Community set não encontrado.")

    r2 = await db.execute(
        select(Configuration.config_text)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    cfg = r2.scalar_one_or_none() or ""
    names = community_list_names_in_config(cfg)
    conflict = s.vrp_object_name in names

    parsed = parse_running_config_communities(cfg)
    samples: list[str] = []
    for ref in parsed.route_policy_if_match[:20]:
        samples.append(f"{ref.route_policy} node {ref.node} → filter {ref.filter_name}")

    o = (getattr(s, "origin", None) or "app_created") or "app_created"
    if s.members:
        mc = len(s.members)
        miss = sum(1 for m in s.members if m.missing_in_library)
        resolved = mc - miss
    elif o in IMPORTED_COMMUNITY_SET_ORIGINS:
        mc = len(s.discovered_members_json or [])
        miss = mc
        resolved = 0
    else:
        mc = len(s.members)
        miss = sum(1 for m in s.members if m.missing_in_library) if s.members else 0
        resolved = mc - miss

    return CommunitySetUsageOut(
        community_set_id=s.id,
        vrp_object_name=s.vrp_object_name,
        vrp_name_conflict_in_saved_config=conflict,
        member_count=mc,
        members_resolved=resolved,
        members_missing=miss,
        route_policy_references_sample=samples,
    )
