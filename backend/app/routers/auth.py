from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..audit_log import log_login_failure, log_login_success
from ..database import get_db
from ..deps.auth import CurrentUserCtx, get_current_active_user
from ..models import User
from ..permissions import permissions_for_role
from ..schemas import LoginRequest, MeResponse, TokenResponse
from ..services.jwt_tokens import create_access_token
from ..services.passwords import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    un = body.username.strip()
    client_ip = request.client.host if request.client else None
    result = await db.execute(select(User).where(User.username == un))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        log_login_failure(username=un, reason="invalid_credentials", client_ip=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha inválidos",
        )
    if not user.is_active:
        log_login_failure(username=un, reason="inactive_user", client_ip=client_ip)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário inativo")
    token = create_access_token(
        subject=str(user.id),
        extra={"role": user.role, "username": user.username},
    )
    log_login_success(
        user_id=user.id,
        username=user.username,
        role=(user.role or "viewer").lower(),
        client_ip=client_ip,
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
async def me(
    ctx: CurrentUserCtx = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).options(selectinload(User.companies)).where(User.id == ctx.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado")
    global_scope = ctx.has_global_company_scope()
    company_ids = [] if global_scope else [c.id for c in user.companies]
    perms = sorted(permissions_for_role(user.role))
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        access_all_companies=bool(getattr(user, "access_all_companies", False)),
        company_ids=company_ids,
        permissions=perms,
    )
