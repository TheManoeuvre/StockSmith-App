from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.colour import Colour
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.colour import ColourCreate, ColourFindOrCreate, ColourRead, ColourUpdate

router = APIRouter(prefix="/colours", tags=["colours"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ColourRead])
async def list_colours(session: AsyncSession = Depends(get_db)) -> list[Colour]:
    return await list_with_usage(session, Colour)


@router.post("", response_model=ColourRead, status_code=status.HTTP_201_CREATED)
async def create_colour(payload: ColourCreate, session: AsyncSession = Depends(get_db)) -> Colour:
    colour = Colour(**payload.model_dump())
    session.add(colour)
    await session.commit()
    await session.refresh(colour)
    return colour


@router.post("/find-or-create", response_model=ColourRead)
async def find_or_create_colour(payload: ColourFindOrCreate, session: AsyncSession = Depends(get_db)) -> Colour:
    """Case-insensitive, unlike the other reference tables' find-or-create.

    Those match exactly because their values were always chosen from a list. Colour was free
    text, so "Black" and "black" already existed side by side — matching exactly here would let
    the duplication the migration just cleaned up start over through the API.
    """
    existing = (
        await session.execute(select(Colour).where(func.lower(Colour.name) == payload.name.strip().lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    colour = Colour(name=payload.name.strip())
    session.add(colour)
    await session.commit()
    await session.refresh(colour)
    return colour


@router.patch("/{row_id}", response_model=ColourRead)
async def update_colour(row_id: int, payload: ColourUpdate, session: AsyncSession = Depends(get_db)) -> Colour:
    return await patch_row(session, Colour, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_colour(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, Colour, row_id)


@router.post("/{row_id}/merge", response_model=ColourRead)
async def merge_colour(row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)) -> Colour:
    return await merge_rows(session, Colour, row_id, payload)
