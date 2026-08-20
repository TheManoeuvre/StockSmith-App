"""Re-reads the discount on eBay orders that were imported before it was being captured.

The adapter used to read `priceDiscountSubtotal` from eBay's pricingSummary. That is not a
field of the Fulfillment API's PricingSummary, so it came back empty on every order —
silently, because a missing key is indistinguishable from an order that simply had no
discount. Meanwhile `priceSubtotal` is the items *before* any discount, so the discount was
neither taken off the subtotal nor recorded anywhere: those orders show more revenue than
the buyer ever paid, and their net profit is overstated by the whole discount.

Order sync will not fix them. `_parse_order` only re-reads an order whose lastModifiedDate
is at or past the sync watermark, so anything imported before the fix stays as it is — the
sync that would correct it is exactly the sync that skips it. Same shape of problem as
scripts/backfill_ebay_fees.py, which exists for the same reason.

Selection is by reconciliation, not by date: an order is a candidate when what StockSmith
has recorded (items + postage + tax) does not equal what eBay says the buyer paid
(grand_total). That makes this self-limiting — orders already correct are never touched —
and safe to re-run.

Writes `subtotal` and `discount_amount` only, and only from eBay's own fields. Nothing is
derived from the gap; the gap only decides who gets looked up.

Usage, from backend/:
    # Show which orders are out and what eBay says, writing nothing.
    uv run python -m scripts.backfill_ebay_discounts

    # Persist it.
    uv run python -m scripts.backfill_ebay_discounts --apply

    # Check every eBay order, not just the ones that fail to reconcile.
    uv run python -m scripts.backfill_ebay_discounts --apply --all
"""

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.order import Order
from app.models.platform_connection import PlatformConnection
from app.services.platforms import get_adapter
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.errors import PlatformError, PlatformSyncError

# Below this and it is rounding, not a discount.
_TOLERANCE = Decimal("0.01")


def _price(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _gap(order: Order) -> Decimal | None:
    """Recorded revenue less what the buyer actually paid. None when unknowable."""
    if order.subtotal is None or order.grand_total is None:
        return None
    recorded = Decimal(order.subtotal) + Decimal(order.shipping_charged or 0) + Decimal(order.tax_charged or 0)
    return recorded - Decimal(order.grand_total)


async def _fetch_order(adapter: EbayAdapter, session, connection: PlatformConnection, external_order_id: str) -> dict:
    """One getOrder call. Deliberately not getOrders-with-a-filter: these orders are old,
    and a date-filtered list query is the thing that skips them."""
    response = await adapter._authed_request(
        session,
        connection,
        "GET",
        f"{adapter.api_base}/sell/fulfillment/v1/order/{external_order_id}",
    )
    if response.status_code != 200:
        raise PlatformSyncError(f"{response.status_code} {response.text}")
    return response.json()


async def _run(args) -> int:
    async with async_session_factory() as session:
        adapter = await get_adapter(session, ListingPlatform.ebay)
        if not isinstance(adapter, EbayAdapter):
            print("eBay adapter unavailable — check Settings > Integrations")
            return 1

        connection = (
            await session.execute(select(PlatformConnection).where(PlatformConnection.platform == ListingPlatform.ebay))
        ).scalar_one_or_none()
        if connection is None or not connection.is_connected:
            print("eBay is not connected")
            return 1

        orders = (
            (await session.execute(select(Order).where(Order.platform == ListingPlatform.ebay).order_by(Order.id)))
            .scalars()
            .all()
        )
        if not args.all:
            orders = [o for o in orders if (gap := _gap(o)) is not None and gap > _TOLERANCE]

        if not orders:
            print("Every eBay order reconciles — nothing to backfill")
            return 0

        print(f"{len(orders)} eBay order(s) to look up{'' if args.apply else ' (dry run — nothing will be written)'}\n")
        updated = 0
        recovered = Decimal(0)
        for order in orders:
            try:
                payload = await _fetch_order(adapter, session, connection, order.external_order_id)
                parsed = await adapter._parse_order(session, connection, payload)
            except PlatformError as e:
                print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
                continue

            was_subtotal = Decimal(order.subtotal) if order.subtotal is not None else None
            new_subtotal = _price(parsed.subtotal)
            new_discount = _price(parsed.discount_amount)

            if new_subtotal is None or was_subtotal == new_subtotal:
                print(f"  #{order.id} {order.external_order_id}: unchanged")
                continue

            delta = was_subtotal - new_subtotal if was_subtotal is not None else Decimal(0)
            recovered += delta
            print(
                f"  #{order.id} {order.external_order_id}: items {was_subtotal} -> {new_subtotal}"
                f"  discount {new_discount}  (net profit falls by {delta})"
            )
            if args.apply:
                order.subtotal = new_subtotal
                order.discount_amount = new_discount
                updated += 1

        if args.apply:
            await session.commit()
            print(f"\nUpdated {updated} order(s); {recovered} of overstated profit corrected")
        else:
            print(f"\nDry run — {recovered} of overstated profit would be corrected. Re-run with --apply to write it.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the fetched values (default: dry run)")
    parser.add_argument(
        "--all", action="store_true", help="look up every eBay order, not just the ones that fail to reconcile"
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
