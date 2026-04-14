from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps.auth import CurrentUserCtx, list_accessible_company_ids, require_permission
from ..models import Company
from ..schemas import CompanyCreate, CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("companies.view"),
):
    ids = await list_accessible_company_ids(db, user)
    if not ids:
        return []
    result = await db.execute(select(Company).where(Company.id.in_(ids)).order_by(Company.name))
    return list(result.scalars().all())


@router.post("/", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("companies.create"),
):
    if not user.is_superadmin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas superadmin pode criar empresas.",
        )
    row = Company(name=payload.name.strip())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("companies.view"),
):
    if not user.can_access_company(company_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a esta empresa")
    result = await db.execute(select(Company).where(Company.id == company_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    return row


@router.put("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("companies.edit"),
):
    if not user.can_access_company(company_id) and not user.is_superadmin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a esta empresa")
    result = await db.execute(select(Company).where(Company.id == company_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    row.name = payload.name.strip()
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUserCtx = require_permission("companies.delete"),
):
    if not user.is_superadmin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Apenas superadmin pode excluir empresas.")
    result = await db.execute(select(Company).where(Company.id == company_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    await db.delete(row)
