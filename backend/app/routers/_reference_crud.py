"""Shared endpoint bodies for the reference-data routers.

Plain functions rather than a router factory: each of manufacturers/suppliers/material-types
keeps its own APIRouter with its own response models (so the OpenAPI schema still names the real
types), and just delegates the bodies here. That keeps the per-resource files short without
hiding their routes behind a layer of generation.
"""

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.services import reference_data
from app.services.reference_data import InUseError, NameConflictError, ReferenceDataError


class MergeRequest(BaseModel):
    target_id: int


def _http_error(exc: ReferenceDataError) -> HTTPException:
    """Map a service error onto a status code.

    409 for both conflict cases — the request is well-formed, it's the current state that makes
    it impossible. The detail is a plain string, deliberately: api/client.ts does
    `throw new ApiError(status, detail || body)`, so a structured object would reach the user as
    "[object Object]". The frontend already holds the full list and can look up the clashing row
    by name locally.
    """
    if isinstance(exc, (NameConflictError, InUseError)):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def list_with_usage(session: AsyncSession, model: type[Base]) -> list:
    """Every row, each carrying how many things reference it.

    The count rides along on the list rather than sitting behind a separate endpoint: it decides
    whether the delete button is even offered, so a list without it can't render correctly.
    """
    rows = list((await session.execute(select(model).order_by(model.name))).scalars())
    counts = await reference_data.usage_counts(session, model)
    for row in rows:
        # Set on the ORM instance so Pydantic's from_attributes picks it up — the column doesn't
        # exist in the database and shouldn't.
        row.usage_count = counts.get(row.id, 0)
    return rows


async def patch_row(session: AsyncSession, model: type[Base], row_id: int, payload) -> object:
    """PATCH one row.

    `exclude_unset=True` is what makes "not mentioned" and "explicitly set to null/false"
    different requests. Without it every optional field on the Update schema arrives with its
    default, so a request naming only `name` would blank every other column — and `rename`
    can no longer defend against that by skipping `None`, because clearing a field is exactly
    what sending `None` is supposed to mean.
    """
    try:
        row = await reference_data.rename(
            session,
            model,
            row_id,
            payload.name,
            **payload.model_dump(exclude={"name"}, exclude_unset=True),
        )
    except ReferenceDataError as exc:
        raise _http_error(exc) from exc
    row.usage_count = (await reference_data.usage_counts(session, model)).get(row.id, 0)
    return row


async def delete_row(session: AsyncSession, model: type[Base], row_id: int) -> None:
    try:
        await reference_data.delete_if_unused(session, model, row_id)
    except ReferenceDataError as exc:
        raise _http_error(exc) from exc


async def merge_rows(session: AsyncSession, model: type[Base], row_id: int, payload: MergeRequest) -> object:
    try:
        target = await reference_data.merge(session, model, row_id, payload.target_id)
    except ReferenceDataError as exc:
        raise _http_error(exc) from exc
    target.usage_count = (await reference_data.usage_counts(session, model)).get(target.id, 0)
    return target
