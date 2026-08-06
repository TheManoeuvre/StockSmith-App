"""Repair OrderKittingAllocation rows for shipped orders whose kitting ledger is missing or
short of what those orders actually consumed.

Two things make this necessary. First, ledger drift is real on existing data — a seeded or
migrated order can be fully shipped with no ledger row at all (services/returns.py's comment
above process_cancellation documents having hit exactly that). Second, packaging cost is now
read from this ledger (kitting.get_kitting_cogs_by_order), so an order with no ledger row
reports no kitting COGS where it previously reported a (wrong, over-counted) figure. This
script is what closes that gap.

DELIBERATELY WRITES ROWS DIRECTLY, NOT VIA reconcile_order_kitting. Calling reconcile here
would be the double-charging bug: it computes consume_delta = target - current, sees the
missing consumption as brand new, and issues a second MaterialAdjustment for stock that
physically left the building at ship time. This script records what was consumed; it does not
consume anything. No MaterialAdjustment is written and no material's current_qty moves.

unit_cost_snapshot is set to the material's CURRENT avg_unit_cost. That's an estimate — the
true cost at the historical ship date isn't recoverable — but it's the same value
get_kitting_cogs_by_order would have fallen back to for a NULL, made explicit and frozen from
here on rather than continuing to drift with every future purchase.

reserved_qty is never touched: reservations belong to live orders, and every order this script
looks at has already shipped.

Side benefit: once repaired, a later reconcile_order_kitting sees current_consumed == target,
produces no delta, and the double-charge hazard on that order is closed for good.

Dry run by default.

Usage, from backend/:
    uv run python scripts/backfill_order_kitting_ledger.py                # dry run
    uv run python scripts/backfill_order_kitting_ledger.py --apply
"""

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Run directly as a file (as the usage line above says), not as a package — so backend/ has
# to go on the path before `app` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.kitting import OrderKittingAllocation  # noqa: E402
from app.models.material import Material  # noqa: E402
from app.models.order import Order, OrderLine  # noqa: E402
from app.services.kitting import _compute_kitting_requirement  # noqa: E402


async def _shipped_orders(session) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.id.in_(select(OrderLine.order_id).where(OrderLine.shipped_qty > 0))).order_by(Order.id)
    )
    return list(result.scalars())


async def run(apply: bool) -> int:
    async with async_session_factory() as session:
        orders = await _shipped_orders(session)
        repaired_orders = 0
        repaired_rows = 0

        for order in orders:
            lines = list(
                (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars()
            )
            target = await _compute_kitting_requirement(session, order.id, lines, lambda l: l.shipped_qty)
            if not target:
                continue

            ledger_rows = {
                row.material_id: row
                for row in (
                    await session.execute(
                        select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id)
                    )
                ).scalars()
            }

            # Only ever raises consumed_qty toward the target. A ledger row already at or
            # above target is left alone — it may legitimately exceed the recomputed figure
            # if an override was lowered after the order shipped, and consumed_qty is the
            # monotonic record of what physically happened.
            shortfalls = [
                (material_id, qty, Decimal(ledger_rows[material_id].consumed_qty) if material_id in ledger_rows else Decimal(0))
                for material_id, qty in target.items()
            ]
            shortfalls = [(mid, qty, current) for mid, qty, current in shortfalls if current < qty]
            if not shortfalls:
                continue

            repaired_orders += 1
            print(f"order #{order.id} ({order.status.value}):")
            for material_id, qty, current in shortfalls:
                material = await session.get(Material, material_id)
                unit_cost = Decimal(material.avg_unit_cost) if material is not None else Decimal(0)
                name = material.name if material is not None else f"material #{material_id}"
                print(f"    {name}: consumed {current} -> {qty} @ {unit_cost}/ea")
                repaired_rows += 1
                if not apply:
                    continue
                ledger = ledger_rows.get(material_id)
                if ledger is None:
                    session.add(
                        OrderKittingAllocation(
                            order_id=order.id,
                            material_id=material_id,
                            reserved_qty=Decimal(0),
                            consumed_qty=qty,
                            unit_cost_snapshot=unit_cost,
                        )
                    )
                else:
                    ledger.consumed_qty = qty
                    if ledger.unit_cost_snapshot is None:
                        ledger.unit_cost_snapshot = unit_cost

        if repaired_orders == 0:
            print("Every shipped order's kitting ledger already matches what it consumed — nothing to do.")
            return 0

        if apply:
            await session.commit()
            print(f"\nRepaired {repaired_rows} ledger row(s) across {repaired_orders} order(s). No stock was moved.")
        else:
            print(
                f"\nDry run — {repaired_rows} ledger row(s) across {repaired_orders} order(s) would be written. "
                "Re-run with --apply."
            )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually write the ledger rows (default: dry run)")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
