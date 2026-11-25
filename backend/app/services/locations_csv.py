"""Locations as a spreadsheet (docs/plan-locations.md §4.1).

One row per location. The path is the identity, in the same human-editable spirit as the other
CSVs (names rather than ids, so a file moves between machines): ``Workshop > Oils rack > Bin 1``.
Importing creates any missing parents along the way, so a file can be just the deepest rows.

Placements are not part of this file: it is the layout, not the stock in it.
"""

import csv
import io
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.services import locations as locs
from app.services.csv_io import _parse_bool

LOCATIONS_CSV_FIELDS = ["path", "code", "notes", "archived"]

# Written with a plain ">" so the file survives any spreadsheet's encoding. Reading also takes the
# "›" the app displays, so a path copied from the screen imports as it looks.
EXPORT_SEPARATOR = " > "
_SPLIT = re.compile(r"\s*[>›]\s*")


def split_path(raw: str) -> list[str]:
    return [part.strip() for part in _SPLIT.split(raw.strip())]


async def export_locations_csv(session: AsyncSession) -> str:
    nodes = await locs.load_nodes(session)
    rows = []
    for node in nodes.values():
        names = locs.path_names(nodes, node.id)
        rows.append((tuple(locs._natural_key(n) for n in names), node, names))
    rows.sort(key=lambda r: r[0])

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=LOCATIONS_CSV_FIELDS)
    writer.writeheader()
    for _, node, names in rows:
        writer.writerow(
            {
                "path": EXPORT_SEPARATOR.join(names),
                "code": node.code,
                "notes": node.notes or "",
                "archived": "yes" if node.archived else "",
            }
        )
    return out.getvalue()


async def _child(session: AsyncSession, parent_id: int | None, name: str) -> Location | None:
    from sqlalchemy import func

    stmt = select(Location).where(func.lower(Location.name) == name.lower())
    stmt = stmt.where(Location.parent_id.is_(None) if parent_id is None else Location.parent_id == parent_id)
    return await session.scalar(stmt.limit(1))


async def import_locations_csv(session: AsyncSession, content: bytes) -> dict:
    """Creates locations that don't exist and updates the notes of ones that do.

    Each row is its own savepoint, so a bad row is reported and skipped without disturbing the
    rest. ``created`` counts every location made, parents included; ``updated`` counts existing
    locations whose notes or archived state changed. The ``code`` column is for reference and is
    never read: codes belong to the database and never change.
    """
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if reader.fieldnames is None or "path" not in [f.strip() for f in reader.fieldnames]:
        raise HTTPException(status_code=400, detail="The file needs a 'path' column.")
    created = 0
    updated = 0
    failed: list[dict] = []

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        raw = (row.get("path") or "").strip()
        if not raw:
            continue  # a blank line in the spreadsheet isn't an error
        try:
            async with session.begin_nested():
                parts = split_path(raw)
                if any(not p for p in parts):
                    raise ValueError("the path has an empty part, like 'A > > B'")
                parent_id: int | None = None
                made = 0
                leaf: Location | None = None
                for depth, name in enumerate(parts):
                    try:
                        name = locs._validate_name(name)
                    except locs.RangeError as exc:
                        raise ValueError(str(exc))
                    existing = await _child(session, parent_id, name)
                    if existing is None:
                        if parent_id is not None:
                            parent = await session.get(Location, parent_id)
                            if parent is not None and parent.archived_at is not None:
                                raise ValueError(f"'{parts[depth - 1]}' is archived, so nothing can be added inside it")
                        existing = await locs._insert(session, parent_id, name, None)
                        made += 1
                    parent_id = existing.id
                    leaf = existing
                assert leaf is not None

                changed = False
                if "notes" in row and row["notes"] is not None and row["notes"].strip() != "":
                    notes = row["notes"].strip()
                    if leaf.notes != notes:
                        leaf.notes = notes
                        changed = True
                archived = (row.get("archived") or "").strip()
                if archived:
                    want = _parse_bool(archived, default=False)
                    if want != (leaf.archived_at is not None):
                        await _set_archived(session, leaf, want)
                        changed = True
            created += made
            if changed and made == 0:
                updated += 1
        except HTTPException as exc:
            failed.append({"row": i, "error": str(exc.detail)})
        except ValueError as exc:
            failed.append({"row": i, "error": str(exc)})

    await session.commit()
    return {"created": created, "updated": updated, "failed": failed}


async def _set_archived(session: AsyncSession, leaf: Location, archive: bool) -> None:
    """Archive or unarchive inside the row's savepoint, with the same rules as the page. The
    service commits, so the rules are re-checked here rather than calling it mid-import."""
    from app.models.location import StockPlacement
    from datetime import datetime, timezone

    if archive:
        if (
            await session.scalar(
                select(StockPlacement.id).where(StockPlacement.location_id == leaf.id, StockPlacement.qty > 0).limit(1)
            )
        ) is not None:
            raise ValueError("it still holds stock, so it can't be archived")
        nodes = await locs.load_nodes(session)
        if [i for i in locs.descendant_ids(nodes, leaf.id) if not nodes[i].archived]:
            raise ValueError("it has locations inside it that aren't archived")
        leaf.archived_at = datetime.now(timezone.utc)
    else:
        if leaf.parent_id is not None:
            parent = await session.get(Location, leaf.parent_id)
            if parent is not None and parent.archived_at is not None:
                raise ValueError("the location it sits in is archived")
        leaf.archived_at = None
