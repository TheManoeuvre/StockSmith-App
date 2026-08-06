"""Re-fetches the marketplace fee breakdown for eBay orders that never got one.

Order sync will not do this on its own. _parse_order only enriches an order whose
lastModifiedDate is at or past the sync watermark (see its `enrich` gate), so orders
imported while the Sell Finances call was failing stay permanently blank: the sync that
would fix them is exactly the sync that skips them.

Every order here was imported with correct totals — only payment_fees/payment_net/
payment_status are missing, and those are the only three columns this writes.

Usage, from backend/:
    # Show what would be fetched and what eBay says, writing nothing.
    uv run python -m scripts.backfill_ebay_fees

    # Persist it.
    uv run python -m scripts.backfill_ebay_fees --apply

    # Re-fetch orders that already have fees too (e.g. after a refund settles).
    uv run python -m scripts.backfill_ebay_fees --apply --all

Read-only by default because it costs one Sell Finances call per order against a fixed
daily API budget, and because a dry run is how you confirm the signing key works before
trusting the numbers it produces.
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
from app.services.platforms.errors import PlatformError


def _price(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


async def _run(args) -> int:
    async with async_session_factory() as session:
        adapter = await get_adapter(session, ListingPlatform.ebay)
        if not isinstance(adapter, EbayAdapter):
            print("eBay adapter unavailable — check Settings > Integrations")
            return 1
        if adapter.signing_key is None:
            print(
                "No eBay signing key is configured, so every fee lookup will 403.\n"
                "Mint one first: Settings > Integrations > eBay."
            )
            return 1

        connection = (
            await session.execute(select(PlatformConnection).where(PlatformConnection.platform == ListingPlatform.ebay))
        ).scalar_one_or_none()
        if connection is None or not connection.is_connected:
            print("eBay is not connected")
            return 1

        query = select(Order).where(Order.platform == ListingPlatform.ebay)
        if not args.all:
            query = query.where(Order.payment_fees.is_(None))
        orders = (await session.execute(query.order_by(Order.id))).scalars().all()

        if not orders:
            print("Nothing to backfill")
            return 0

        print(f"{len(orders)} eBay order(s) to look up{'' if args.apply else ' (dry run — nothing will be written)'}\n")
        updated = 0
        for order in orders:
            try:
                fees, net, payment_status = await adapter._fetch_transactions(
                    session, connection, order.external_order_id
                )
            except PlatformError as e:
                print(f"  #{order.id} {order.external_order_id}: FAILED — {e}")
                continue

            if fees is None:
                print(f"  #{order.id} {order.external_order_id}: no fee data returned")
                continue

            print(f"  #{order.id} {order.external_order_id}: fees {fees}  net {net}  status {payment_status}")
            if args.apply:
                order.payment_fees = _price(fees)
                order.payment_net = _price(net)
                order.payment_status = payment_status
                updated += 1

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
        "--all", action="store_true", help="include orders that already have fees, not just the blank ones"
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
