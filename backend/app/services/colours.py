"""Resolving a colour name to its reference row.

Kept out of the router so the CSV importer and the materials endpoints share one definition of
"what does this colour string mean".
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.colour import Colour


async def find_or_create(session: AsyncSession, name: str | None) -> Colour | None:
    """Match an existing colour case-insensitively, or create one.

    Case-insensitive on purpose: the whole reason this table exists is that free text
    accumulated "Black", "black" and "BLACK" as separate colours. Matching exactly would let the
    same duplication start over through the API the migration just cleaned up.

    Does not commit — the caller owns the transaction, so a material and its new colour land
    together or not at all.
    """
    if name is None:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None

    existing = (
        await session.execute(select(Colour).where(func.lower(Colour.name) == cleaned.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    colour = Colour(name=cleaned)
    session.add(colour)
    await session.flush()
    return colour


async def resolve_updates(session: AsyncSession, updates: dict) -> dict:
    """Turn a `colour` name in a payload into a `colour_id`, keeping both columns in step.

    Both are written during the transition: `colour_id` is the real reference, and the legacy
    `colour` text column stays populated so a rollback to the previous release — or a backup
    restored into it — still shows colours.
    """
    if "colour_id" in updates and updates["colour_id"] is not None:
        colour = await session.get(Colour, updates["colour_id"])
        updates["colour"] = colour.name if colour else None
        return updates

    if "colour" in updates:
        colour = await find_or_create(session, updates.get("colour"))
        updates["colour_id"] = colour.id if colour else None
        updates["colour"] = colour.name if colour else None

    return updates
