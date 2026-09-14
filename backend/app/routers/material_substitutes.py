from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.build import Build
from app.models.material import Material
from app.models.material_substitute import MaterialSubstitute, MaterialSubstituteUsage
from app.models.order import Order
from app.schemas.material_substitute import (
    MaterialSubstituteCreate,
    MaterialSubstituteRead,
    MaterialSubstituteUpdate,
    MaterialSubstituteUsageCreate,
    MaterialSubstituteUsageRead,
)
from app.services.material_substitutes import record_substitute_usage

# Scoped to require_auth, same gate as materials.py — this app has a single shared
# password rather than per-user roles (see deps.require_auth), so "admin/staff" here is
# exactly "authenticated at all", matching every other material-management endpoint.
router = APIRouter(
    prefix="/materials/{material_id}/substitutes", tags=["material-substitutes"], dependencies=[Depends(require_auth)]
)

# Usage logging lives at its own top-level prefix rather than nested under a single
# material_id, since a usage record names two materials (the short one and the one used
# instead) plus an order/build, and neither is more "primary" than the other for lookup.
usage_router = APIRouter(
    prefix="/material-substitute-usage", tags=["material-substitutes"], dependencies=[Depends(require_auth)]
)


def _to_read(sub: MaterialSubstitute) -> MaterialSubstituteRead:
    return MaterialSubstituteRead.model_validate(sub).model_copy(
        update={
            "substitute_material_name": sub.substitute_material.name if sub.substitute_material else None,
        }
    )


async def _get_material_or_404(session: AsyncSession, material_id: int) -> Material:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    return material


async def _get_substitute_or_404(session: AsyncSession, material_id: int, substitute_id: int) -> MaterialSubstitute:
    result = await session.execute(
        select(MaterialSubstitute)
        .where(MaterialSubstitute.id == substitute_id, MaterialSubstitute.material_id == material_id)
        .options(selectinload(MaterialSubstitute.substitute_material))
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Substitute not found")
    return sub


@router.get("", response_model=list[MaterialSubstituteRead])
async def list_material_substitutes(
    material_id: int, session: AsyncSession = Depends(get_db)
) -> list[MaterialSubstituteRead]:
    await _get_material_or_404(session, material_id)
    result = await session.execute(
        select(MaterialSubstitute)
        .where(MaterialSubstitute.material_id == material_id)
        .options(selectinload(MaterialSubstitute.substitute_material))
        .order_by(MaterialSubstitute.rank, MaterialSubstitute.id)
    )
    return [_to_read(sub) for sub in result.scalars()]


@router.post("", response_model=MaterialSubstituteRead, status_code=status.HTTP_201_CREATED)
async def add_material_substitute(
    material_id: int, payload: MaterialSubstituteCreate, session: AsyncSession = Depends(get_db)
) -> MaterialSubstituteRead:
    await _get_material_or_404(session, material_id)
    if payload.substitute_material_id == material_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A material cannot substitute for itself"
        )
    await _get_material_or_404(session, payload.substitute_material_id)

    existing = await session.execute(
        select(MaterialSubstitute).where(
            MaterialSubstitute.material_id == material_id,
            MaterialSubstitute.substitute_material_id == payload.substitute_material_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material {payload.substitute_material_id} is already a substitute for material {material_id}",
        )

    sub = MaterialSubstitute(
        material_id=material_id,
        substitute_material_id=payload.substitute_material_id,
        rank=payload.rank,
        notes=payload.notes,
        created_by=payload.created_by,
    )
    session.add(sub)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material {payload.substitute_material_id} is already a substitute for material {material_id}",
        )
    await session.refresh(sub)
    return _to_read(await _get_substitute_or_404(session, material_id, sub.id))


@router.patch("/{substitute_id}", response_model=MaterialSubstituteRead)
async def update_material_substitute(
    material_id: int, substitute_id: int, payload: MaterialSubstituteUpdate, session: AsyncSession = Depends(get_db)
) -> MaterialSubstituteRead:
    """Handles both halves of "reorder/deactivate" from the CRUD requirement: PATCHing
    `rank` reorders a material's fallback list, PATCHing `is_active` deactivates (or
    reactivates) one without losing its history."""
    sub = await _get_substitute_or_404(session, material_id, substitute_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(sub, field, value)
    await session.commit()
    return _to_read(await _get_substitute_or_404(session, material_id, substitute_id))


@usage_router.post("", response_model=MaterialSubstituteUsageRead, status_code=status.HTTP_201_CREATED)
async def create_material_substitute_usage(
    payload: MaterialSubstituteUsageCreate, session: AsyncSession = Depends(get_db)
) -> MaterialSubstituteUsage:
    await _get_material_or_404(session, payload.material_id)
    await _get_material_or_404(session, payload.substitute_material_id)
    if payload.order_id is not None and await session.get(Order, payload.order_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if payload.build_id is not None and await session.get(Build, payload.build_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build not found")

    return await record_substitute_usage(
        session,
        material_id=payload.material_id,
        substitute_material_id=payload.substitute_material_id,
        qty=payload.qty,
        order_id=payload.order_id,
        build_id=payload.build_id,
        notes=payload.notes,
        created_by=payload.created_by,
    )


@usage_router.get("", response_model=list[MaterialSubstituteUsageRead])
async def list_material_substitute_usage(
    material_id: int | None = None,
    order_id: int | None = None,
    build_id: int | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[MaterialSubstituteUsage]:
    query = select(MaterialSubstituteUsage).order_by(MaterialSubstituteUsage.created_at.desc())
    if material_id is not None:
        query = query.where(MaterialSubstituteUsage.material_id == material_id)
    if order_id is not None:
        query = query.where(MaterialSubstituteUsage.order_id == order_id)
    if build_id is not None:
        query = query.where(MaterialSubstituteUsage.build_id == build_id)
    result = await session.execute(query)
    return list(result.scalars())
