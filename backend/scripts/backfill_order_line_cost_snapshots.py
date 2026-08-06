"""Backfill cost_per_unit_snapshot for OrderLines that were already allocated (or shipped)
before COGS snapshotting moved from line-creation time to first-allocation time (see
app/services/allocation.py::_allocate_line).

Before that change, a line's cost was captured once at import/creation — permanently
frozen at whatever a material's avg_unit_cost happened to be at that instant. An order
imported before its product's BOM was fully costed (or before a component material had
ever had a purchase recorded, so avg_unit_cost was still 0) ended up with a
cost_per_unit_snapshot stuck at 0/NULL forever, even after the material's cost later
became real — nothing in the codebase ever revisited it. This script is the one-off
remediation: it recomputes the snapshot, as if first-allocation snapshotting had been in
effect all along, for every already-allocated line still missing one.

Only touches lines with allocated_qty > 0 (the analogue of "has been allocated at least
once") and no snapshot — an unallocated line is correctly left alone, since its real
snapshot will be captured whenever it's actually allocated, and a line that already has a
snapshot (zero or otherwise) is left as-is rather than silently overwritten.

Materials only. Packaging cost is an order-level figure on the kitting ledger, not a line
snapshot — see scripts/backfill_order_kitting_ledger.py for its counterpart.

Dry run by default.

Usage, from backend/:
    uv run python scripts/backfill_order_line_cost_snapshots.py                # dry run
    uv run python scripts/backfill_order_line_cost_snapshots.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Run directly as a file (as the usage line above says), not as a package — so backend/ has
# to go on the path before `app` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.order import OrderLine  # noqa: E402
from app.services.order_costs import compute_line_cost_snapshot  # noqa: E402


async def _find_candidates(session) -> list[OrderLine]:
    result = await session.execute(
        select(OrderLine).where(
            OrderLine.allocated_qty > 0,
            OrderLine.cost_per_unit_snapshot.is_(None),
            OrderLine.product_id.isnot(None),
        )
    )
    return list(result.scalars())


async def run(apply: bool) -> int:
    async with async_session_factory() as session:
        candidates = await _find_candidates(session)
        if not candidates:
            print("No allocated OrderLines with a missing cost snapshot — nothing to do.")
            return 0

        print(f"{len(candidates)} allocated line(s) with no cost snapshot:")
        updated = 0
        for line in candidates:
            cost_per_unit = await compute_line_cost_snapshot(session, line.product_id, line.variant_id)
            still_unknown = cost_per_unit is None
            print(
                f"  order_line #{line.id} (order #{line.order_id}, product #{line.product_id}, "
                f"variant #{line.variant_id}): cost_per_unit={cost_per_unit}"
                + ("  [still uncostable — no build BOM]" if still_unknown else "")
            )
            if apply and not still_unknown:
                line.cost_per_unit_snapshot = cost_per_unit
                updated += 1

        if apply:
            await session.commit()
            print(f"\nUpdated {updated} line(s).")
        else:
            print(f"\nDry run — {len(candidates)} candidate(s) found. Re-run with --apply to write.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually write the recomputed snapshots (default: dry run)")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
