"""Backfills orders.tracking_number/carrier and order_lines.variation_text for orders
imported BEFORE those fields existed.

Order sync only ever sets these on a fresh call to _parse_order/_parse_receipt — an
already-imported order never revisits eBay's shipping_fulfillment sub-resource or Etsy's
receipt shipments/variations once its lines exist (_upsert_lines only ever creates
lines, never mutates them; _apply_financials only writes tracking_number when the
adapter actually fetched one). Every order/line imported before this feature shipped is
therefore permanently blank without a one-off pass like this one.

eBay: only tracking_number/carrier exist for eBay (no personalization concept), fetched
via the same shipping_fulfillment call order_sync now makes for newly-shipped orders.

Etsy: a single per-receipt GET (with includes=Transactions,Shipments) covers both
tracking (receipt.shipments) and personalization (transaction.variations) in one call,
so both are backfilled together per order.

Usage, from backend/:
    # Show what would be fetched and what each marketplace says, writing nothing.
    uv run python -m scripts.backfill_order_tracking_and_variations

    # Persist it.
    uv run python -m scripts.backfill_order_tracking_and_variations --apply

    # Re-fetch orders/lines that already have a value too (e.g. to pick up a correction).
    uv run python -m scripts.backfill_order_tracking_and_variations --apply --all

    # Just one marketplace.
    uv run python -m scripts.backfill_order_tracking_and_variations --apply --platform etsy

Read-only by default: this costs one API call per order against each marketplace's daily
budget, and a dry run is how you confirm connectivity before trusting the numbers.
"""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.order import Order, OrderLine, OrderStatus
from app.models.platform_connection import PlatformConnection
from app.services.platforms import get_adapter
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.errors import PlatformError
from app.services.platforms.etsy import EtsyAdapter


async def _get_connection(session, platform: ListingPlatform) -> PlatformConnection | None:
    connection = (
        await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    ).scalar_one_or_none()
    if connection is None or not connection.is_connected:
        print(f"{platform.value} is not connected — skipping")
        return None
    return connection


async def _backfill_ebay(session, args) -> int:
    adapter = await get_adapter(session, ListingPlatform.ebay)
    if not isinstance(adapter, EbayAdapter):
        print("eBay adapter unavailable — check Settings > Integrations")
        return 0
    connection = await _get_connection(session, ListingPlatform.ebay)
    if connection is None:
        return 0

    query = select(Order).where(Order.platform == ListingPlatform.ebay, Order.status == OrderStatus.shipped)
    if not args.all:
        query = query.where(Order.tracking_number.is_(None))
    orders = (await session.execute(query.order_by(Order.id))).scalars().all()

    if not orders:
        print("eBay: nothing to backfill")
        return 0

    print(f"eBay: {len(orders)} shipped order(s) to look up")
    updated = 0
    for order in orders:
        try:
            tracking_number, carrier = await adapter._fetch_tracking(session, connection, order.external_order_id)
        except PlatformError as e:
            print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
            continue

        if not tracking_number:
            print(f"  #{order.id} {order.external_order_id}: no tracking number returned")
            continue

        print(f"  #{order.id} {order.external_order_id}: {carrier or '?'} {tracking_number}")
        if args.apply:
            order.tracking_number = tracking_number
            order.carrier = carrier
            updated += 1
    return updated


async def _backfill_etsy(session, args) -> int:
    adapter = await get_adapter(session, ListingPlatform.etsy)
    if not isinstance(adapter, EtsyAdapter):
        print("Etsy adapter unavailable — check Settings > Integrations")
        return 0
    connection = await _get_connection(session, ListingPlatform.etsy)
    if connection is None:
        return 0

    query = (
        select(Order)
        .where(Order.platform == ListingPlatform.etsy)
        .options(selectinload(Order.lines))
        .order_by(Order.id)
    )
    orders = (await session.execute(query)).scalars().all()

    # Decided in Python rather than SQL — "does this order have anything missing" needs
    # to look at every line, and there are far fewer orders than would justify an EXISTS
    # subquery here.
    def needs_backfill(order: Order) -> bool:
        if args.all:
            return True
        if order.status == OrderStatus.shipped and order.tracking_number is None:
            return True
        return any(line.variation_text is None for line in order.lines)

    candidates = [o for o in orders if needs_backfill(o)]
    if not candidates:
        print("Etsy: nothing to backfill")
        return 0

    print(f"Etsy: {len(candidates)} order(s) to look up")
    updated = 0
    for order in candidates:
        try:
            response = await adapter._authed_request(
                session,
                connection,
                "GET",
                f"/shops/{connection.external_account_id}/receipts/{order.external_order_id}",
                params={"includes": "Transactions,Shipments"},
            )
        except PlatformError as e:
            print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
            continue
        if response.status_code != 200:
            print(f"  #{order.id} {order.external_order_id}: FAILED — {response.status_code} {response.text[:200]}")
            continue

        # getShopReceipt returns the receipt object directly; only Etsy's list endpoints
        # wrap in {count, results}. Accept either shape defensively rather than assume.
        body = response.json()
        results = body.get("results")
        receipt = results[0] if isinstance(results, list) and results else body

        changed = []

        shipments = receipt.get("shipments") or []
        first_shipment = shipments[0] if shipments else {}
        tracking_number = first_shipment.get("tracking_code")
        carrier = first_shipment.get("carrier_name")
        if tracking_number:
            changed.append(f"tracking {carrier or '?'} {tracking_number}")
            if args.apply:
                order.tracking_number = tracking_number
                order.carrier = carrier

        lines_by_external_id = {line.external_line_id: line for line in order.lines}
        for tx in receipt.get("transactions") or []:
            line = lines_by_external_id.get(str(tx.get("transaction_id")))
            if line is None:
                continue
            variation_text = EtsyAdapter._format_variations(tx.get("variations"))
            if variation_text:
                changed.append(f"line #{line.id} personalization {variation_text!r}")
                if args.apply:
                    line.variation_text = variation_text

        if not changed:
            print(f"  #{order.id} {order.external_order_id}: nothing new")
            continue
        print(f"  #{order.id} {order.external_order_id}: " + "; ".join(changed))
        updated += 1
    return updated


async def _run(args) -> int:
    async with async_session_factory() as session:
        updated = 0
        if args.platform in ("ebay", "both"):
            updated += await _backfill_ebay(session, args)
        if args.platform in ("etsy", "both"):
            updated += await _backfill_etsy(session, args)

        if args.apply:
            await session.commit()
            print(f"\nUpdated {updated} order(s)")
        else:
            print("\nDry run — re-run with --apply to write these")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the fetched values (default: dry run)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="re-check orders/lines that already have a value, not just the blank ones",
    )
    parser.add_argument("--platform", choices=["ebay", "etsy", "both"], default="both")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
