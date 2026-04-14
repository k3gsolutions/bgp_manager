from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import Company, Device, User
from ..permissions import role_has_permission
from ..services.jwt_tokens import parse_user_id_from_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUserCtx:
    id: int
    username: str
    role: str
    company_ids: tuple[int, ...]
    access_all_companies: bool = False

    def is_superadmin(self) -> bool:
        return (self.role or "").lower() == "superadmin"

    def has_global_company_scope(self) -> bool:
        """Superadmin ou flag explícita de acesso a todas as empresas (dispositivos)."""
        return self.is_superadmin() or self.access_all_companies

    def has_perm(self, perm: str) -> bool:
        return role_has_permission(self.role, perm)

    def can_access_company(self, company_id: int | None) -> bool:
        if self.has_global_company_scope():
            return True
        if company_id is None:
            return False
        return int(company_id) in self.company_ids

    def can_access_device(self, device: Device) -> bool:
        return self.can_access_company(getattr(device, "company_id", None))


def _perm_checker(permission: str) -> Callable[..., CurrentUserCtx]:
    async def _inner(user: CurrentUserCtx = Depends(get_current_active_user)) -> CurrentUserCtx:
        if not role_has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permissão necessária: {permission}",
            )
        return user

    return _inner


def require_permission(permission: str):
    """Retorna um Depends(...) que exige a permissão nomeada."""
    return Depends(_perm_checker(permission))


async def get_current_user_ctx(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> CurrentUserCtx:
    if cred is None or not cred.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    uid = parse_user_id_from_token(cred.credentials)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(
        select(User)
        .options(selectinload(User.companies))
        .where(User.id == uid)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inválido ou inativo")
    is_super = (user.role or "").lower() == "superadmin"
    if is_super:
        cids: tuple[int, ...] = ()
        aac = False
    else:
        cids = tuple(sorted(c.id for c in user.companies))
        aac = bool(getattr(user, "access_all_companies", False))
    return CurrentUserCtx(
        id=user.id,
        username=user.username,
        role=(user.role or "viewer").lower(),
        company_ids=cids,
        access_all_companies=aac,
    )


async def get_current_active_user(
    user: CurrentUserCtx = Depends(get_current_user_ctx),
) -> CurrentUserCtx:
    return user


async def get_device_for_user(
    device_id: int,
    db: AsyncSession,
    user: CurrentUserCtx,
) -> Device:
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispositivo não encontrado")
    if not user.can_access_device(device):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a este dispositivo")
    return device


def scoped_device_ids_subquery(user: CurrentUserCtx):
    """Subconsulta de ids de dispositivos no escopo do usuário."""
    if user.has_global_company_scope():
        return select(Device.id)
    if not user.company_ids:
        return select(Device.id).where(Device.id == -1)
    return select(Device.id).where(Device.company_id.in_(user.company_ids))


async def list_accessible_company_ids(db: AsyncSession, user: CurrentUserCtx) -> list[int]:
    if user.has_global_company_scope():
        res = await db.execute(select(Company.id).order_by(Company.name))
        return [r[0] for r in res.all()]
    return list(user.company_ids)
