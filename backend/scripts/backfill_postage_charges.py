"""Backfills order_postage_charges (marketplace shipping labels) for orders that shipped
BEFORE label detection existed, or that a later label purchase didn't get re-synced.

Order sync only asks a marketplace about an order's labels on a pass that re-fetches
that order — eBay's getOrders / Etsy's getShopReceipts filtered by last-modified — and
only when the enrichment gate is open (see EbayAdapter._parse_order and
EtsyAdapter._parse_receipt). Buying a second label adds a shipment to the order, which
is expected to bump the marketplace's last-modified date and reopen that gate; but an
order that shipped before this feature landed has already aged out of the window, and
if a later label ever fails to touch last-modified it would be missed the same way.
This one-off pass asks each marketplace directly.

Everything found goes through the same order_parcels.apply_postage_charges the sync
uses, so the rules are identical: label #1 becomes the order's actual postage cost,
each later label links to an existing unlinked replacement parcel or creates a
needs_review one and raises the review alert (under --apply).

Usage, from backend/:
    # Show what each marketplace reports, writing nothing.
    uv run python -m scripts.backfill_postage_charges

    # Persist it.
    uv run python -m scripts.backfill_postage_charges --apply

    # Re-check orders that already have at least one label recorded.
    uv run python -m scripts.backfill_postage_charges --apply --all

    # Just one marketplace.
    uv run python -m scripts.backfill_postage_charges --apply --platform ebay

Read-only by default: this costs one API call per order (eBay: Sell Finances
getTransactions; Etsy: the payments lookup plus a paged 30-day ledger crawl) against
each marketplace's daily budget, and a dry run is how you confirm connectivity before
trusting the numbers. Etsy's ledger label matching is best-effort — see
EtsyAdapter._extract_postage_charges — and the dry run prints the diagnostic line it
logs for unrecognised ledger entries, which is how to confirm the shape on real data.
"""

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.order import Order, OrderStatus
from app.models.platform_connection import PlatformConnection
from app.services import order_parcels
from app.services.platforms import get_adapter
from app.services.platforms.base import ExternalPostageCharge
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


async def _candidates(session, platform: ListingPlatform, include_all: bool) -> list[Order]:
    query = (
        select(Order)
        .where(Order.platform == platform, Order.status == OrderStatus.shipped)
        .options(selectinload(Order.postage_charges))
        .order_by(Order.id)
    )
    orders = (await session.execute(query)).scalars().all()
    return [o for o in orders if include_all or not o.postage_charges]


async def _report_and_apply(session, order: Order, found: list[ExternalPostageCharge], apply: bool) -> bool:
    known = {c.external_id for c in order.postage_charges}
    new = [c for c in found if c.external_id not in known]
    if not new:
        print(f"  #{order.id} {order.external_order_id}: {len(found)} label(s), nothing new")
        return False
    print(
        f"  #{order.id} {order.external_order_id}: "
        + "; ".join(f"label {c.external_id} {c.amount} {c.currency or ''} {c.posted_at:%Y-%m-%d}" if c.posted_at else f"label {c.external_id} {c.amount}" for c in new)
    )
    if apply:
        pending = await order_parcels.apply_postage_charges(session, order, found)
        # The session isn't committed until the end of the run; alerts commit on their
        # own, so they're only sent once everything else is on disk (see _run).
        _PENDING_ALERTS.extend(pending)
    return True


_PENDING_ALERTS: list = []


async def _backfill_ebay(session, args) -> int:
    connection = await _get_connection(session, ListingPlatform.ebay)
    if connection is None:
        return 0
    adapter = await get_adapter(session, ListingPlatform.ebay)
    if not isinstance(adapter, EbayAdapter):
        print("eBay adapter unavailable — check Settings > Integrations")
        return 0
    orders = await _candidates(session, ListingPlatform.ebay, args.all)
    if not orders:
        print("eBay: nothing to backfill")
        return 0
    print(f"eBay: {len(orders)} order(s) to look up")
    updated = 0
    for order in orders:
        try:
            _fees, _net, _status, labels = await adapter._fetch_transactions(session, connection, order.external_order_id)
        except PlatformError as e:
            print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
            continue
        if await _report_and_apply(session, order, labels, args.apply):
            updated += 1
    return updated


async def _backfill_etsy(session, args) -> int:
    connection = await _get_connection(session, ListingPlatform.etsy)
    if connection is None:
        return 0
    adapter = await get_adapter(session, ListingPlatform.etsy)
    if not isinstance(adapter, EtsyAdapter):
        print("Etsy adapter unavailable — check Settings > Integrations")
        return 0
    orders = await _candidates(session, ListingPlatform.etsy, args.all)
    if not orders:
        print("Etsy: nothing to backfill")
        return 0
    print(f"Etsy: {len(orders)} order(s) to look up")
    updated = 0
    for order in orders:
        try:
            response = await adapter._authed_request(
                session,
                connection,
                "GET",
                f"/shops/{connection.external_account_id}/receipts/{order.external_order_id}",
                params={"includes": "Transactions"},
            )
            if response.status_code != 200:
                print(f"  #{order.id} {order.external_order_id}: FAILED — {response.status_code} {response.text[:200]}")
                continue
            body = response.json()
            results = body.get("results")
            receipt = results[0] if isinstance(results, list) and results else body
            transactions = receipt.get("transactions") or []
            _fees, _net, _status, payment_id = await adapter._fetch_payment(session, connection, receipt.get("receipt_id"))
            _total, labels = await adapter._fetch_platform_fees_total(session, connection, receipt, transactions, payment_id)
        except PlatformError as e:
            print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
            continue
        if await _report_and_apply(session, order, labels, args.apply):
            updated += 1
    return updated


async def _run(args) -> int:
    # Surface the Etsy ledger diagnostic (logged at INFO) on the console — the whole
    # point of a dry run against Etsy is to read it.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    async with async_session_factory() as session:
        updated = 0
        if args.platform in ("ebay", "both"):
            updated += await _backfill_ebay(session, args)
        if args.platform in ("etsy", "both"):
            updated += await _backfill_etsy(session, args)

        if args.apply:
            await session.commit()
            await order_parcels.raise_pending_review_alerts(session, _PENDING_ALERTS)
            print(f"\nUpdated {updated} order(s); {len(_PENDING_ALERTS)} replacement parcel(s) need completing")
        else:
            print("\nDry run — re-run with --apply to write these")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the fetched labels (default: dry run)")
    parser.add_argument("--all", action="store_true", help="re-check orders that already have a label recorded")
    parser.add_argument("--platform", choices=["ebay", "etsy", "both"], default="both")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
