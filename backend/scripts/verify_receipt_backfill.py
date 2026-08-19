"""Prove the receipts migration did not change a single material's stock or cost.

Splitting receiving out of purchases.status rewrote the one query every material's
current_qty and avg_unit_cost is derived from (services/costing.py's replay), and moved
the timestamp it costs at from the order to the delivery. On data that predates receipts
those two should be exactly equivalent — every already-received line got one receipt
covering it in full, at the order's own received_at — but "should be" is not a thing to
take on trust when the numbers are somebody's inventory valuation.

So: read every material, replay it under the new code, and compare against what is stored.

**Exact equality, not a tolerance.** A tolerance here would hide precisely the bug worth
finding. The cost apportionment is written so that a single receipt covering a whole line
is handed the line total untouched rather than multiplied and divided back to itself, and
if that path is wrong the difference shows up in the far decimals first — which is where a
tolerance would swallow it and let the error compound on the next purchase.

Read-only: nothing is committed, and the recomputed values are compared in memory and
rolled back. Point it at a copy anyway — the app writes to its database while it runs, and
a comparison against a moving target proves nothing.

Usage, from backend/:
    # copy the live database first (the app can be running; this is a consistent snapshot)
    python -c "import sqlite3; s=sqlite3.connect('file:SRC?mode=ro',uri=True); d=sqlite3.connect('copy.db'); s.backup(d)"
    DATABASE_URL=sqlite+aiosqlite:///./copy.db uv run python scripts/verify_receipt_backfill.py

Exit code is 0 when every material matches, 1 when any does not.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Run directly as a file (as the usage line above says), not as a package — so backend/ has
# to go on the path before `app` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.material import Material  # noqa: E402
from app.services.costing import recompute_material  # noqa: E402


async def main() -> int:
    async with async_session_factory() as session:
        materials = list((await session.execute(select(Material).order_by(Material.id))).scalars())
        if not materials:
            print("No materials in this database — nothing to verify.")
            return 0

        # Snapshot before anything recomputes, because recompute_material assigns straight
        # onto the ORM object and the stored value would otherwise be gone by comparison time.
        stored = {m.id: (Decimal(m.current_qty), Decimal(m.avg_unit_cost), m.name) for m in materials}

        for material in materials:
            await recompute_material(session, material.id)

        # Compare what would be *stored*, not what is momentarily in memory. avg_unit_cost is
        # a Numeric(14,6), so a freshly computed Decimal carries a tail the column does not:
        # 0.02298999999999999843680598133 and 0.022990 are the same number arriving from two
        # different sides of the write. Flushing and expiring puts both readings through the
        # identical round trip, which is the only comparison that means anything.
        await session.flush()
        session.expire_all()
        replayed = list((await session.execute(select(Material).order_by(Material.id))).scalars())

        differences = []
        for material in replayed:
            was_qty, was_cost, name = stored[material.id]
            now_qty = Decimal(material.current_qty)
            now_cost = Decimal(material.avg_unit_cost)
            if now_qty != was_qty or now_cost != was_cost:
                differences.append((material.id, name, was_qty, now_qty, was_cost, now_cost))

        # Never commit. The point is to compare, not to repair — a repair here would erase
        # the evidence of whatever went wrong.
        await session.rollback()

    print(f"Checked {len(materials)} materials.")
    if not differences:
        print("Every material's current_qty and avg_unit_cost replays identically. ")
        return 0

    print(f"\n{len(differences)} material(s) DIFFER — do not ship this:\n")
    for material_id, name, was_qty, now_qty, was_cost, now_cost in differences:
        print(f"  #{material_id} {name}")
        if now_qty != was_qty:
            print(f"      qty:  stored {was_qty}  ->  replayed {now_qty}")
        if now_cost != was_cost:
            print(f"      cost: stored {was_cost}  ->  replayed {now_cost}")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
