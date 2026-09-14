"""Backfills orders.tracking_number/carrier, orders.ship_by_date and
order_lines.variation_text for orders imported BEFORE those fields existed, or whose
value was computed wrong at the time.

Order sync only ever sets these on a fresh call to _parse_order/_parse_receipt — an
already-imported order never revisits eBay's shipping_fulfillment sub-resource or Etsy's
receipt shipments/variations/expected_ship_date once its lines exist (_upsert_lines only
ever creates lines, never mutates them). _apply_financials does refresh ship_by_date on
every later sync, but only for orders a sync actually re-fetches (min_last_modified) —
an order with no recent activity is skipped and stays stale. Every order/line imported
before a feature shipped, or last touched before a parsing bug was fixed, is therefore
permanently wrong without a one-off pass like this one.

Etsy's ship_by_date and variation_text both had real bugs, not just a "field didn't
exist yet" gap, which is why this script's default (non---all) selection now also
targets already-populated rows:
  - ship_by_date used to be read from receipt.expected_ship_date, a field that does not
    exist anywhere on Etsy's ShopReceipt schema (only ShopReceiptTransaction has it) —
    so it was silently None on every Etsy order until the parsing fix, regardless of
    when the order was imported.
  - variation_text used to keep every entry in transaction.variations, including real
    product options (Colour, Size) that Etsy returns in the same array as buyer
    personalization — see EtsyAdapter._format_variations. A previously-populated line
    can therefore hold a real option mislabeled as personalization, not just be blank.

eBay: no personalization concept, and its ship_by_date parsing was always correct (see
EbayAdapter._ship_by_date_from_line_items) — but an order imported before that field
existed, or not touched by a sync since, still has it stuck at None the same way
tracking_number does. tracking_number comes from the shipping_fulfillment sub-resource
(only meaningful once shipped); ship_by_date comes from a separate getOrder call
(meaningful on any non-cancelled order) — up to two calls per order, only the ones
actually needed.

Etsy: a single per-receipt GET (with includes=Transactions,Shipments) covers tracking
(receipt.shipments), ship_by_date and personalization (both from transactions) in one
call, so all three are backfilled together per order.

Usage, from backend/:
    # Show what would be fetched and what each marketplace says, writing nothing.
    uv run python -m scripts.backfill_order_tracking_and_variations

    # Persist it.
    uv run python -m scripts.backfill_order_tracking_and_variations --apply

    # Re-fetch orders/lines that already have a value too (e.g. to pick up any other
    # correction beyond the two known Etsy bugs above, which are always rechecked).
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

    query = select(Order).where(Order.platform == ListingPlatform.ebay)
    if not args.all:
        # tracking_number is only ever missing, never wrong, so it stays gated on the
        # shipped+blank condition. ship_by_date's gap is the same "field didn't exist
        # yet" kind as tracking (eBay's own parsing of it was always correct — see
        # module docstring) — but unlike tracking it's meaningful on unshipped orders
        # too, so it's checked regardless of shipped status.
        query = query.where(
            ((Order.status == OrderStatus.shipped) & Order.tracking_number.is_(None))
            | ((Order.status != OrderStatus.cancelled) & Order.ship_by_date.is_(None))
        )
    orders = (await session.execute(query.order_by(Order.id))).scalars().all()

    if not orders:
        print("eBay: nothing to backfill")
        return 0

    print(f"eBay: {len(orders)} order(s) to look up")
    updated = 0
    for order in orders:
        changed = []
        needs_tracking = order.status == OrderStatus.shipped and (args.all or order.tracking_number is None)
        needs_ship_by_date = order.status != OrderStatus.cancelled and (args.all or order.ship_by_date is None)

        if needs_tracking:
            try:
                tracking_number, carrier = await adapter._fetch_tracking(session, connection, order.external_order_id)
            except PlatformError as e:
                print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
                continue
            if tracking_number:
                changed.append(f"tracking {carrier or '?'} {tracking_number}")
                if args.apply:
                    order.tracking_number = tracking_number
                    order.carrier = carrier

        if needs_ship_by_date:
            try:
                raw_order = await adapter.fetch_order(session, connection, order.external_order_id)
            except PlatformError as e:
                print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
                continue
            if raw_order is not None:
                ship_by_date = EbayAdapter._ship_by_date_from_line_items(raw_order.get("lineItems", []))
                if ship_by_date != order.ship_by_date:
                    changed.append(f"ship_by_date {order.ship_by_date} -> {ship_by_date}")
                    if args.apply:
                        order.ship_by_date = ship_by_date

        if not changed:
            print(f"  #{order.id} {order.external_order_id}: nothing new")
            continue
        print(f"  #{order.id} {order.external_order_id}: " + "; ".join(changed))
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
    #
    # ship_by_date and variation_text are always rechecked, --all or not, even when
    # already set — both had genuine parsing bugs (see module docstring), not just a
    # "field didn't exist yet" gap, so an existing value can be actively wrong rather
    # than merely blank. tracking_number is the one field that's only ever missing, never
    # wrong, so it stays gated on --all like before.
    def needs_backfill(order: Order) -> bool:
        if args.all:
            return True
        if order.status == OrderStatus.shipped and order.tracking_number is None:
            return True
        if order.status != OrderStatus.cancelled and order.ship_by_date is None:
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
        transactions = receipt.get("transactions") or []

        shipments = receipt.get("shipments") or []
        first_shipment = shipments[0] if shipments else {}
        tracking_number = first_shipment.get("tracking_code")
        carrier = first_shipment.get("carrier_name")
        if tracking_number:
            changed.append(f"tracking {carrier or '?'} {tracking_number}")
            if args.apply:
                order.tracking_number = tracking_number
                order.carrier = carrier

        # Always recomputed and compared against the stored value, not just filled in
        # when blank — expected_ship_date used to be read off the wrong object entirely
        # (see module docstring), so an already-set ship_by_date can be wrong too, not
        # just missing.
        ship_by_date = EtsyAdapter._ship_by_date_from_transactions(transactions)
        if ship_by_date != order.ship_by_date:
            changed.append(f"ship_by_date {order.ship_by_date} -> {ship_by_date}")
            if args.apply:
                order.ship_by_date = ship_by_date

        lines_by_external_id = {line.external_line_id: line for line in order.lines}
        for tx in transactions:
            line = lines_by_external_id.get(str(tx.get("transaction_id")))
            if line is None:
                continue
            # Compared against the stored value rather than gated on "is there a new
            # value", same reason as ship_by_date above: a previously-stored
            # variation_text can be a real product option mislabeled as personalization
            # (see module docstring), and the corrected value for that case is None —
            # `if variation_text:` would silently refuse to ever clear that.
            variation_text = EtsyAdapter._format_variations(tx.get("variations"))
            if variation_text != line.variation_text:
                changed.append(f"line #{line.id} personalization {line.variation_text!r} -> {variation_text!r}")
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
