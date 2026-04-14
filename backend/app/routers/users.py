from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..deps.auth import CurrentUserCtx, require_permission
from ..models import Company, User
from ..schemas import (
    UserCompaniesPatch,
    UserCreate,
    UserPasswordPatch,
    UserResponse,
    UserUpdate,
)
from ..services.passwords import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


def _to_user_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id,
        username=u.username,
        role=u.role,
        is_active=u.is_active,
        access_all_companies=bool(getattr(u, "access_all_companies", False)),
        company_ids=[c.id for c in u.companies],
        created_at=u.created_at,
        updated_at=u.updated_at,
    )


async def _load_user(db: AsyncSession, user_id: int) -> User | None:
    r = await db.execute(select(User).options(selectinload(User.companies)).where(User.id == user_id))
    return r.scalar_one_or_none()


def _actor_may_manage_target(actor: CurrentUserCtx, target: User) -> None:
    if actor.is_superadmin():
        return
    if (target.role or "").lower() == "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão sobre superadmin")
    if getattr(target, "access_all_companies", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas superadmin pode gerir usuários com acesso a todos os clientes",
        )
    if (actor.role or "").lower() != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")
    t_ids = {c.id for c in target.companies}
    a_ids = set(actor.company_ids)
    if not t_ids.issubset(a_ids) and t_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrador só altera usuários do seu conjunto de empresas",
        )


def _validate_company_ids_for_actor(actor: CurrentUserCtx, ids: list[int], db_companies_ok: set[int]) -> None:
    if actor.is_superadmin():
        for cid in ids:
            if cid not in db_companies_ok:
                raise HTTPException(status_code=400, detail=f"Empresa inválida: {cid}")
        return
    allowed = set(actor.company_ids)
    for cid in ids:
        if cid not in allowed:
            raise HTTPException(status_code=403, detail=f"Sem acesso à empresa {cid}")


@router.get("/", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.view"),
):
    stmt = select(User).options(selectinload(User.companies)).order_by(User.username)
    if actor.is_superadmin():
        result = await db.execute(stmt)
        return [_to_user_response(u) for u in result.scalars().all()]
    if (actor.role or "").lower() == "admin":
        ids = set(actor.company_ids)
        if not ids:
            return []
        users = (await db.execute(stmt)).scalars().all()
        out: list[User] = []
        for u in users:
            if (u.role or "").lower() == "superadmin":
                continue
            if getattr(u, "access_all_companies", False):
                continue
            uc = {c.id for c in u.companies}
            if not uc or uc & ids:
                out.append(u)
        return [_to_user_response(u) for u in out]
    u = await _load_user(db, actor.id)
    return [_to_user_response(u)] if u else []


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.create"),
):
    if (payload.role or "").lower() == "superadmin" and not actor.is_superadmin():
        raise HTTPException(status_code=403, detail="Apenas superadmin pode criar outro superadmin")
    existing = await db.execute(select(User).where(User.username == payload.username.strip()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username já existe")

    comp_res = await db.execute(select(Company.id))
    all_cids = {r[0] for r in comp_res.all()}
    is_super_role = (payload.role or "").lower() == "superadmin"
    want_all = bool(payload.access_all_companies) and not is_super_role
    if want_all and not actor.is_superadmin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas superadmin pode conceder acesso a todos os clientes",
        )
    if not is_super_role and not want_all and not payload.company_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Selecione ao menos uma empresa ou marque acesso a todos os clientes",
        )
    if not is_super_role and not want_all:
        _validate_company_ids_for_actor(actor, payload.company_ids, all_cids)

    u = User(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
        access_all_companies=False,
    )
    if is_super_role:
        u.access_all_companies = False
        u.companies = []
    elif want_all:
        u.access_all_companies = True
        u.companies = []
    else:
        u.access_all_companies = False
        comps = (await db.execute(select(Company).where(Company.id.in_(payload.company_ids)))).scalars().all()
        u.companies = list(comps)
    db.add(u)
    await db.flush()
    await db.refresh(u)
    r2 = await _load_user(db, u.id)
    return _to_user_response(r2)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.view"),
):
    u = await _load_user(db, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if actor.id != user_id and not actor.is_superadmin() and (actor.role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Sem permissão")
    if (actor.role or "").lower() == "admin":
        _actor_may_manage_target(actor, u)
    return _to_user_response(u)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.edit"),
):
    u = await _load_user(db, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    _actor_may_manage_target(actor, u)
    if payload.role and (payload.role or "").lower() == "superadmin" and not actor.is_superadmin():
        raise HTTPException(status_code=403, detail="Apenas superadmin pode promover a superadmin")
    if payload.username is not None:
        u.username = payload.username.strip()
    if payload.role is not None:
        u.role = payload.role
        if (payload.role or "").lower() == "superadmin":
            u.access_all_companies = False
            u.companies = []
    if payload.is_active is not None:
        u.is_active = payload.is_active
    if payload.access_all_companies is not None:
        if (u.role or "").lower() == "superadmin":
            u.access_all_companies = False
        elif payload.access_all_companies and not actor.is_superadmin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas superadmin pode conceder acesso a todos os clientes",
            )
        elif payload.access_all_companies:
            u.access_all_companies = True
            u.companies = []
        else:
            u.access_all_companies = False
    await db.flush()
    r2 = await _load_user(db, u.id)
    return _to_user_response(r2)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.delete"),
):
    if user_id == actor.id:
        raise HTTPException(status_code=400, detail="Não é possível excluir a si mesmo")
    u = await _load_user(db, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    _actor_may_manage_target(actor, u)
    await db.delete(u)


@router.patch("/{user_id}/companies", response_model=UserResponse)
async def patch_user_companies(
    user_id: int,
    payload: UserCompaniesPatch,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.edit"),
):
    u = await _load_user(db, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    _actor_may_manage_target(actor, u)
    if (u.role or "").lower() == "superadmin":
        u.companies = []
        u.access_all_companies = False
    elif payload.access_all_companies is True:
        if not actor.is_superadmin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Apenas superadmin pode conceder acesso a todos os clientes",
            )
        u.access_all_companies = True
        u.companies = []
    elif payload.access_all_companies is False:
        u.access_all_companies = False
        comp_res = await db.execute(select(Company.id))
        all_cids = {r[0] for r in comp_res.all()}
        _validate_company_ids_for_actor(actor, payload.company_ids, all_cids)
        if not payload.company_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Informe ao menos uma empresa ao desmarcar acesso a todos os clientes",
            )
        comps = (await db.execute(select(Company).where(Company.id.in_(payload.company_ids)))).scalars().all()
        u.companies = list(comps)
    else:
        comp_res = await db.execute(select(Company.id))
        all_cids = {r[0] for r in comp_res.all()}
        _validate_company_ids_for_actor(actor, payload.company_ids, all_cids)
        if not payload.company_ids and not u.access_all_companies:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Selecione ao menos uma empresa",
            )
        if payload.company_ids:
            u.access_all_companies = False
            comps = (await db.execute(select(Company).where(Company.id.in_(payload.company_ids)))).scalars().all()
            u.companies = list(comps)
    await db.flush()
    r2 = await _load_user(db, u.id)
    return _to_user_response(r2)


@router.patch("/{user_id}/password", response_model=UserResponse)
async def patch_user_password(
    user_id: int,
    payload: UserPasswordPatch,
    db: AsyncSession = Depends(get_db),
    actor: CurrentUserCtx = require_permission("users.edit"),
):
    u = await _load_user(db, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if user_id == actor.id:
        u.password_hash = hash_password(payload.password)
    else:
        _actor_may_manage_target(actor, u)
        u.password_hash = hash_password(payload.password)
    await db.flush()
    r2 = await _load_user(db, u.id)
    return _to_user_response(r2)
