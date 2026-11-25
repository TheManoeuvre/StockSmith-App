"""Storage locations: the tree, the range generator, moves (docs/plan-locations.md §3, §4.1, §4.2).

Placements — how much of each item is in a location — live in services/placements.py.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location, PlacementEvent, StockPlacement

MAX_RANGE_LOCATIONS = 500
MAX_NAME_LENGTH = 100
PATH_SEPARATOR = " › "


# --- Range patterns -------------------------------------------------------------------------


class RangeError(ValueError):
    """The pattern can't be read or would make too many names. The message is user-facing."""


def _alpha_to_number(s: str) -> int:
    n = 0
    for ch in s.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _number_to_alpha(n: int, upper: bool) -> str:
    out = ""
    while n > 0:
        n -= 1
        out = chr(65 + n % 26) + out
        n //= 26
    return out if upper else out.lower()


_NUMERIC_TOKEN = re.compile(r"^(\d+)\s*-\s*(\d+)$")
_ALPHA_TOKEN = re.compile(r"^([A-Za-z]+)\s*-\s*([A-Za-z]+)$")
_BRACES = re.compile(r"\{([^{}]*)\}")


def _expand_token(token: str) -> list[str]:
    m = _NUMERIC_TOKEN.match(token)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        # Padding follows the start value: {01-05} gives 01..05, {1-5} gives 1..5.
        width = len(m.group(1)) if len(m.group(1)) > 1 and m.group(1)[0] == "0" else 0
        if end < start:
            raise RangeError(f"The end of {{{token}}} comes before its start.")
        if end - start + 1 > MAX_RANGE_LOCATIONS:
            raise RangeError(
                f"That would make {end - start + 1:,} locations. The limit is {MAX_RANGE_LOCATIONS} at a time."
            )
        return [str(i).zfill(width) for i in range(start, end + 1)]
    m = _ALPHA_TOKEN.match(token)
    if m:
        start, end = _alpha_to_number(m.group(1)), _alpha_to_number(m.group(2))
        upper = m.group(1) == m.group(1).upper()
        if end < start:
            raise RangeError(f"The end of {{{token}}} comes before its start.")
        if end - start + 1 > MAX_RANGE_LOCATIONS:
            raise RangeError(
                f"That would make {end - start + 1:,} locations. The limit is {MAX_RANGE_LOCATIONS} at a time."
            )
        return [_number_to_alpha(i, upper) for i in range(start, end + 1)]
    raise RangeError(f"I can’t read {{{token}}}. Try {{01-05}} or {{A-F}}.")


def expand_pattern(pattern: str) -> list[str]:
    """Turns ``Box{01-05}`` or ``{A-D}{1-6}`` into the names it stands for.

    Pure and shared by the preview and the create call, so the two cannot disagree. Raises
    RangeError with a message fit to show the user.
    """
    tokens = _BRACES.findall(pattern)
    if not tokens:
        raise RangeError("Put a range in braces, like {01-05} or {A-F}.")
    if len(tokens) > 2:
        raise RangeError("Use at most two ranges in one pattern.")
    if re.search(r"[{}]", _BRACES.sub("", pattern)):
        raise RangeError("The braces in that pattern don’t match up.")
    lists = [_expand_token(t) for t in tokens]
    total = 1
    for lst in lists:
        total *= len(lst)
    if total > MAX_RANGE_LOCATIONS:
        raise RangeError(f"That would make {total:,} locations. The limit is {MAX_RANGE_LOCATIONS} at a time.")

    pieces = _BRACES.split(pattern)  # literal, token, literal, token, literal
    names = [""]
    for i, piece in enumerate(pieces):
        if i % 2 == 0:
            names = [n + piece for n in names]
        else:
            values = lists[i // 2]
            names = [n + v for n in names for v in values]
    seen: set[str] = set()
    unique = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            unique.append(n)
    for n in unique:
        _validate_name(n)
    return unique


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise RangeError("A location needs a name.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise RangeError(f"Names can be at most {MAX_NAME_LENGTH} characters.")
    return cleaned


# --- Tree -----------------------------------------------------------------------------------


@dataclass
class LocationNode:
    id: int
    parent_id: int | None
    name: str
    code: str
    notes: str | None
    archived: bool


async def load_nodes(session: AsyncSession) -> dict[int, LocationNode]:
    rows = (await session.scalars(select(Location))).all()
    return {
        r.id: LocationNode(r.id, r.parent_id, r.name, r.code, r.notes, r.archived_at is not None) for r in rows
    }


def _natural_key(name: str) -> list:
    """Natural sort key, so Box2 comes before Box10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.split(r"(\d+)", name) if p != ""]


def path_names(nodes: dict[int, LocationNode], location_id: int) -> list[str]:
    names: list[str] = []
    seen: set[int] = set()
    current: int | None = location_id
    while current is not None and current in nodes and current not in seen:
        seen.add(current)
        names.append(nodes[current].name)
        current = nodes[current].parent_id
    return list(reversed(names))


def path_ids(nodes: dict[int, LocationNode], location_id: int) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    current: int | None = location_id
    while current is not None and current in nodes and current not in seen:
        seen.add(current)
        ids.append(current)
        current = nodes[current].parent_id
    return list(reversed(ids))


def descendant_ids(nodes: dict[int, LocationNode], location_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for n in nodes.values():
        children.setdefault(n.parent_id, []).append(n.id)
    out: set[int] = set()
    stack = [location_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def display_path(nodes: dict[int, LocationNode], location_id: int) -> str:
    return PATH_SEPARATOR.join(path_names(nodes, location_id))


async def get_location(session: AsyncSession, location_id: int) -> Location:
    location = await session.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    return location


async def _require_parent(session: AsyncSession, parent_id: int | None) -> Location | None:
    if parent_id is None:
        return None
    parent = await get_location(session, parent_id)
    if parent.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You can’t add to an archived location. Unarchive it first."
        )
    return parent


async def _name_taken(session: AsyncSession, parent_id: int | None, name: str, *, except_id: int | None = None) -> bool:
    stmt = select(Location.id).where(func.lower(Location.name) == name.lower())
    stmt = stmt.where(Location.parent_id.is_(None) if parent_id is None else Location.parent_id == parent_id)
    if except_id is not None:
        stmt = stmt.where(Location.id != except_id)
    return (await session.scalar(stmt.limit(1))) is not None


def _code_for(location_id: int) -> str:
    return f"LOC-{location_id:06d}"


async def _insert(session: AsyncSession, parent_id: int | None, name: str, notes: str | None) -> Location:
    location = Location(parent_id=parent_id, name=name, notes=notes, code=f"TMP-{uuid.uuid4().hex}")
    session.add(location)
    await session.flush()
    # The code comes from the row id, so it is short, unique and never needs to be looked up.
    location.code = _code_for(location.id)
    await session.flush()
    return location


async def create_location(
    session: AsyncSession, name: str, parent_id: int | None, notes: str | None = None
) -> Location:
    try:
        cleaned = _validate_name(name)
    except RangeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await _require_parent(session, parent_id)
    if await _name_taken(session, parent_id, cleaned):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"There is already a location called “{cleaned}” here."
        )
    location = await _insert(session, parent_id, cleaned, (notes or "").strip() or None)
    await session.commit()
    await session.refresh(location)
    return location


@dataclass
class RangePreviewName:
    name: str
    exists: bool


@dataclass
class RangePreview:
    names: list[RangePreviewName]
    error: str | None = None

    @property
    def new_count(self) -> int:
        return sum(1 for n in self.names if not n.exists)


async def preview_range(session: AsyncSession, parent_id: int | None, pattern: str) -> RangePreview:
    try:
        names = expand_pattern(pattern)
    except RangeError as exc:
        return RangePreview(names=[], error=str(exc))
    stmt = select(func.lower(Location.name)).where(
        Location.parent_id.is_(None) if parent_id is None else Location.parent_id == parent_id
    )
    existing = set((await session.scalars(stmt)).all())
    return RangePreview(names=[RangePreviewName(n, n.lower() in existing) for n in names])


async def generate_range(session: AsyncSession, parent_id: int | None, pattern: str) -> list[Location]:
    await _require_parent(session, parent_id)
    preview = await preview_range(session, parent_id, pattern)
    if preview.error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=preview.error)
    created: list[Location] = []
    for item in preview.names:
        if item.exists:
            continue  # skipped, never overwritten
        created.append(await _insert(session, parent_id, item.name, None))
    if not created:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Every one of those already exists here.")
    await session.commit()
    for loc in created:
        await session.refresh(loc)
    return created


async def update_location(
    session: AsyncSession, location_id: int, *, name: str | None = None, notes: str | None = None, set_notes: bool = False
) -> Location:
    location = await get_location(session, location_id)
    if name is not None:
        try:
            cleaned = _validate_name(name)
        except RangeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        if cleaned.lower() != location.name.lower() and await _name_taken(
            session, location.parent_id, cleaned, except_id=location.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"There is already a location called “{cleaned}” here."
            )
        location.name = cleaned
    if set_notes:
        location.notes = (notes or "").strip() or None
    await session.commit()
    await session.refresh(location)
    return location


async def move_location(session: AsyncSession, location_id: int, new_parent_id: int | None) -> Location:
    """Re-parents a location. Its contents travel with it, because placements point at the
    location, not at the path."""
    location = await get_location(session, location_id)
    if location.parent_id == new_parent_id:
        return location
    nodes = await load_nodes(session)
    if new_parent_id is not None:
        await _require_parent(session, new_parent_id)
        if new_parent_id == location_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A location can’t be moved into itself.")
        if new_parent_id in descendant_ids(nodes, location_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A location can’t be moved inside one of its own sub-locations.",
            )
    if await _name_taken(session, new_parent_id, location.name, except_id=location.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"There is already a location called “{location.name}” in the destination.",
        )
    location.parent_id = new_parent_id
    await session.commit()
    await session.refresh(location)
    return location


async def _has_stock(session: AsyncSession, location_id: int) -> bool:
    return (
        await session.scalar(
            select(StockPlacement.id).where(StockPlacement.location_id == location_id, StockPlacement.qty > 0).limit(1)
        )
    ) is not None


async def set_archived(session: AsyncSession, location_id: int, archived: bool) -> Location:
    location = await get_location(session, location_id)
    if archived:
        if location.archived_at is not None:
            return location
        if await _has_stock(session, location_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Move the stock out of this location before archiving it."
            )
        nodes = await load_nodes(session)
        live_below = [i for i in descendant_ids(nodes, location_id) if not nodes[i].archived]
        if live_below:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Archive or move the locations inside this one first.",
            )
        # Zero-quantity rows don't count as stock but would keep an archived place "in use".
        for row in (
            await session.scalars(select(StockPlacement).where(StockPlacement.location_id == location_id))
        ).all():
            await session.delete(row)
        location.archived_at = datetime.now(timezone.utc)
    else:
        if location.parent_id is not None:
            parent = await session.get(Location, location.parent_id)
            if parent is not None and parent.archived_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Unarchive the location it was inside first."
                )
        location.archived_at = None
    await session.commit()
    await session.refresh(location)
    return location


async def delete_location(session: AsyncSession, location_id: int) -> None:
    """Only a location that was never used can be deleted; anything else is archived, so old
    labels and history keep meaning something."""
    location = await get_location(session, location_id)
    in_use = (
        await session.scalar(select(Location.id).where(Location.parent_id == location_id).limit(1))
    ) is not None or (
        await session.scalar(select(StockPlacement.id).where(StockPlacement.location_id == location_id).limit(1))
    ) is not None or (
        await session.scalar(
            select(PlacementEvent.id)
            .where(
                (PlacementEvent.from_location_id == location_id) | (PlacementEvent.to_location_id == location_id)
            )
            .limit(1)
        )
    ) is not None
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This location has been used, so it can only be archived, not deleted.",
        )
    try:
        await session.delete(location)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This location is still in use.")
