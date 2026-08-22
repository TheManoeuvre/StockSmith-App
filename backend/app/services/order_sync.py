import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.order import Order, OrderLine, OrderStatus
from app.models.platform_connection import PlatformConnection
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.platform import SyncCommitResult, SyncPreviewLine, SyncPreviewOrder, SyncPreviewResult
from app.services import allocation
from app.services.order_costs import default_order_shipping_profile
from app.services.platforms import get_adapter
from app.services.platforms.base import ExternalOrder, PaymentState, ensure_utc
from app.services.variants import find_by_sku

logger = logging.getLogger("stocksmith.order_sync")

_PLATFORM_LABELS: dict[ListingPlatform, str] = {
    ListingPlatform.etsy: "Etsy",
    ListingPlatform.ebay: "eBay",
    ListingPlatform.shopify: "Shopify",
}

# How far back the unpaid hold is willing to keep the fetch window open for an order that
# still hasn't been paid. Past this, an unsettled order stops holding the window and is
# recoverable only if the platform bumps its last_modified when payment finally lands
# (which both platforms are believed to do — the hold exists precisely because "believed
# to" isn't good enough to risk losing a paid order over). An order left unpaid for 30
# continuous days is abandoned in practice; falling out is logged at WARNING so it's
# never silent.
_MAX_UNPAID_HOLD = timedelta(days=30)


async def _get_connection(session: AsyncSession, platform: ListingPlatform) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None or not connection.is_connected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{platform.value} is not connected")
    return connection


def _effective_since(connection: PlatformConnection) -> datetime | None:
    """Never fetch orders unmodified since sync_start_date, regardless of how far
    last_orders_synced_at has advanced — this is the user-configurable floor that keeps
    a shop's entire historical backlog out of the first (and every subsequent) sync.
    Passed to fetch_orders_since as a min_last_modified filter, not min_created, so an
    order's status change (shipped/cancelled) is what re-surfaces it, not just its
    original placement date — an old order that gets a fresh modification (e.g. a late
    cancellation) will still surface even though it predates sync_start_date; that's
    intentional, since the modification itself is a new event happening now."""
    floor = (
        datetime.combine(connection.sync_start_date, time.min, tzinfo=timezone.utc)
        if connection.sync_start_date is not None
        else None
    )
    # SQLite doesn't reliably round-trip an aware datetime through SQLAlchemy — a value
    # this app itself wrote via the ORM can come back naive on a later read (see
    # platforms/base.ensure_utc's own docstring); every stored datetime here is UTC
    # regardless, so it's always safe to reattach tzinfo before comparing.
    last_orders_synced_at = ensure_utc(connection.last_orders_synced_at)

    # The unpaid hold drags the query window back far enough to keep re-seeing an order
    # that was skipped for being unpaid, so that whenever it does settle we notice. It is
    # kept as its own column rather than by rewinding last_orders_synced_at, because the
    # watermark means "everything up to here has been fully processed" — the adapters'
    # enrichment cutoff depends on that meaning, and PlatformStatus surfaces it to the
    # user, where a value that appears to travel backwards would just look broken.
    unpaid_hold_since = ensure_utc(connection.unpaid_hold_since)
    if unpaid_hold_since is not None:
        last_orders_synced_at = (
            unpaid_hold_since if last_orders_synced_at is None else min(last_orders_synced_at, unpaid_hold_since)
        )

    if last_orders_synced_at is None:
        return floor
    if floor is None:
        return last_orders_synced_at
    # The sync_start_date floor still wins, so the hold can never reach further back than
    # the cutoff the user configured.
    return max(floor, last_orders_synced_at)


async def _get_existing_external_ids(
    session: AsyncSession, platform: ListingPlatform, external_orders: list[ExternalOrder]
) -> set[str]:
    if not external_orders:
        return set()
    result = await session.execute(
        select(Order.external_order_id).where(
            Order.platform == platform,
            Order.external_order_id.in_([o.external_order_id for o in external_orders]),
        )
    )
    return {row[0] for row in result if row[0] is not None}


def _drop_unknown_orders_predating_cutoff(
    connection: PlatformConnection, external_orders: list[ExternalOrder], existing_ids: set[str]
) -> list[ExternalOrder]:
    """The settings page promises "orders placed before this date are never imported,
    regardless of sync history" — but fetch_orders_since filters on min_last_modified,
    not placement date (see _effective_since), so an order placed before sync_start_date
    can still come back if it was modified more recently (e.g. it shipped). That's fine
    for an order we already know about (its status/financials should still refresh), but
    a brand-new one has to be dropped here or the "never imported" promise breaks."""
    if connection.sync_start_date is None:
        return external_orders
    return [
        o
        for o in external_orders
        if o.external_order_id in existing_ids or o.placed_at.date() >= connection.sync_start_date
    ]


def _partition_new_orders(
    platform: ListingPlatform, external_orders: list[ExternalOrder], existing_ids: set[str], *, log: bool = True
) -> tuple[list[ExternalOrder], list[ExternalOrder]]:
    """Splits the fetched batch into (import, skip-as-unpaid).

    The rule: an order StockSmith has never seen is imported only once the marketplace
    says the money actually arrived. An unpaid order doesn't become a pending record, a
    staging row, or a greyed-out entry — it simply doesn't exist here yet. That matters
    because importing one isn't cosmetic: allocate_order reserves product stock, reserves
    packaging materials via kitting.reconcile_order_kitting, and through
    listing_push.enqueue_for_owner pushes REDUCED QUANTITIES TO THE LIVE MARKETPLACE
    LISTINGS seconds later. An order that may never be paid for can therefore take real
    stock off sale.

    Deliberately conditioned on existing_ids, which is why this lives here and not in the
    adapters: an already-imported order is never skipped, no matter what its payment says.
    It still has to flow through _reconcile_status so a later shipment, cancellation, or
    payment reversal is picked up. Mirrors _drop_unknown_orders_predating_cutoff, which
    makes the same fetch-it-then-conditionally-exclude-it move for the same reason.

    PaymentState.reversed is NOT skipped — a refunded order is real history worth keeping,
    it just must never reserve anything. _reconcile_status handles that by importing it
    flagged and unallocated.

    `log` is off for preview, which calls this purely to annotate what a real sync would
    do — writing "skipped" lines for a dry run would make backend.log claim imports were
    withheld that were never going to happen.
    """
    keep: list[ExternalOrder] = []
    skipped: list[ExternalOrder] = []
    for order in external_orders:
        if order.external_order_id not in existing_ids and order.payment_state is PaymentState.unsettled:
            skipped.append(order)
            if log:
                # Logged per-order because the alternative — a shop that looks quiet — is
                # indistinguishable from a gate that has started wrongly rejecting
                # everything. Both raw fields are included so a surprise response shape is
                # diagnosable from backend.log without re-running anything.
                logger.info(
                    "%s order %s skipped: payment not settled (is_paid=%r, status=%r)",
                    _PLATFORM_LABELS.get(platform, platform.value),
                    order.external_order_id,
                    order.raw.get("is_paid"),
                    order.raw.get("status") or order.raw.get("orderPaymentStatus"),
                )
        else:
            keep.append(order)
    return keep, skipped


def _compute_unpaid_hold(skipped: list[ExternalOrder]) -> datetime | None:
    """Where the fetch window must stay open back to, so that a skipped unpaid order is
    seen again on every later poll until it either settles or ages out.

    Returns None when nothing was skipped — the hold is self-clearing, so it costs
    nothing on the overwhelming majority of polls where every order is paid.

    Guards against the one way this could go wrong: an order that stays unpaid forever
    would otherwise pin the window open forever, and each poll re-fetches everything in
    it. _MAX_UNPAID_HOLD bounds that.
    """
    now = datetime.now(timezone.utc)
    fresh: list[datetime] = []
    for order in skipped:
        last_modified = ensure_utc(order.last_modified)
        if now - last_modified <= _MAX_UNPAID_HOLD:
            fresh.append(last_modified)
        else:
            logger.warning(
                "Order %s has been unpaid for over %d days — no longer holding the sync window open for it. "
                "If it is ever paid, it will only be re-imported if the marketplace updates its last-modified time.",
                order.external_order_id,
                _MAX_UNPAID_HOLD.days,
            )
    if not fresh:
        return None
    # One second before, so the >= comparison the platforms apply to min_last_modified
    # can't exclude the very order being held for.
    return min(fresh) - timedelta(seconds=1)


async def _resolve_line_match(
    session: AsyncSession, platform: ListingPlatform | None, sku: str | None
) -> tuple[int | None, int | None]:
    if not sku:
        return None, None
    match = await find_by_sku(session, sku, platform)
    return match if match is not None else (None, None)


async def _resolve_names(session: AsyncSession, product_id: int | None, variant_id: int | None) -> tuple[str | None, str | None]:
    product_name = variant_name = None
    if product_id is not None:
        product = await session.get(Product, product_id)
        product_name = product.name if product else None
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        variant_name = variant.variant_name if variant else None
    return product_name, variant_name


async def preview_sync(session: AsyncSession, platform: ListingPlatform) -> SyncPreviewResult:
    """Fetches orders and shows what a commit would do, without writing any order/line
    data — the only DB write here is the sync-run log entry itself. Safe to run
    repeatedly against a live store."""
    connection = await _get_connection(session, platform)
    adapter = await get_adapter(session, platform)

    run = PlatformSyncRun(platform=platform, mode=SyncRunMode.preview, status=SyncRunStatus.success)
    session.add(run)

    try:
        external_orders = await adapter.fetch_orders_since(session, connection, _effective_since(connection))
        existing_ids = await _get_existing_external_ids(session, platform, external_orders)
        external_orders = _drop_unknown_orders_predating_cutoff(connection, external_orders, existing_ids)

        # Preview deliberately does NOT drop unpaid orders the way commit_sync does — it
        # annotates them instead. The whole point of preview is to see ground truth before
        # trusting the sync, and "what is the gate holding back, and does the marketplace's
        # own is_paid field agree with our reading of it" is exactly the question a human
        # needs to answer here. Each order carries its untouched `raw` payload alongside
        # would_import for precisely that comparison.
        _, skipped_unpaid = _partition_new_orders(platform, external_orders, existing_ids, log=False)
        skipped_ids = {o.external_order_id for o in skipped_unpaid}

        preview_orders: list[SyncPreviewOrder] = []
        needs_mapping_count = 0
        new_count = 0

        for ext_order in external_orders:
            already_imported = ext_order.external_order_id in existing_ids
            if not already_imported:
                new_count += 1

            preview_lines = []
            for line in ext_order.lines:
                product_id, variant_id = await _resolve_line_match(session, platform, line.sku)
                if product_id is None and variant_id is None:
                    needs_mapping_count += 1
                product_name, variant_name = await _resolve_names(session, product_id, variant_id)
                preview_lines.append(
                    SyncPreviewLine(
                        external_line_id=line.external_line_id,
                        sku=line.sku,
                        qty=line.qty,
                        matched_product_id=product_id,
                        matched_product_name=product_name,
                        matched_variant_id=variant_id,
                        matched_variant_name=variant_name,
                    )
                )

            preview_orders.append(
                SyncPreviewOrder(
                    external_order_id=ext_order.external_order_id,
                    buyer_name=ext_order.buyer_name,
                    placed_at=ext_order.placed_at,
                    is_cancelled=ext_order.is_cancelled,
                    is_shipped=ext_order.is_shipped,
                    already_imported=already_imported,
                    payment_state=ext_order.payment_state.value,
                    would_import=ext_order.external_order_id not in skipped_ids,
                    lines=preview_lines,
                    raw=ext_order.raw,
                )
            )

        run.fetched_count = len(external_orders)
        run.new_count = new_count
        run.needs_mapping_count = needs_mapping_count
        run.skipped_unpaid_count = len(skipped_unpaid)
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()

        return SyncPreviewResult(
            fetched_count=len(external_orders),
            new_count=new_count,
            needs_mapping_count=needs_mapping_count,
            skipped_unpaid_count=len(skipped_unpaid),
            orders=preview_orders,
        )
    except Exception as e:
        await session.rollback()
        await _record_failure(session, platform, SyncRunMode.preview, e)
        raise


async def commit_sync(platform: ListingPlatform) -> SyncCommitResult:
    """Idempotent upsert of fetched orders on (platform, external_order_id). New orders
    are allocated immediately; Etsy-side cancellations/shipments are reconciled for
    orders already known to us. The DB-write portion is one transaction — a failure
    partway through it rolls back entirely rather than leaving a half-imported batch.

    Deliberately manages its own two short-lived sessions rather than taking one from
    the caller: fetch_orders_since can involve dozens of paginated HTTP round-trips to
    the marketplace plus retry/backoff sleeps on 409/429 responses (see the Etsy/eBay
    adapters), and holding a single pooled DB connection open across all of that starved
    the app's whole connection pool — the same anti-pattern already fixed in
    services/listing_push.py, just triggered by this running for both platforms on every
    app boot (sync_scheduler) instead of by a stock-change fan-out. The fetch-phase
    session is closed (and its pooled connection released) before the write phase opens
    its own — the write phase never needs anything beyond the plain rows/IDs fetch_orders_
    since already returned, so nothing is lost by not sharing a session across the two."""
    try:
        async with async_session_factory() as fetch_session:
            connection = await _get_connection(fetch_session, platform)
            adapter = await get_adapter(fetch_session, platform)
            raw_external_orders = await adapter.fetch_orders_since(
                fetch_session, connection, _effective_since(connection)
            )

        async with async_session_factory() as session:
            # Reloaded fresh rather than reusing the fetch-phase `connection` — that
            # instance is detached now that its session is closed, and this is cheap (a
            # single PK lookup) compared to the fetch phase it's decoupled from.
            connection = await _get_connection(session, platform)
            existing_ids = await _get_existing_external_ids(session, platform, raw_external_orders)
            external_orders = _drop_unknown_orders_predating_cutoff(connection, raw_external_orders, existing_ids)
            external_orders, skipped_unpaid = _partition_new_orders(platform, external_orders, existing_ids)

            created_count = 0
            updated_count = 0
            needs_mapping_count = 0
            shipped_count = 0
            order_ids: list[int] = []

            for ext_order in external_orders:
                order, is_new = await _upsert_order(session, platform, ext_order)
                if is_new:
                    created_count += 1
                else:
                    updated_count += 1

                line_needs_mapping = await _upsert_lines(session, order, ext_order)
                needs_mapping_count += line_needs_mapping
                await session.flush()

                just_shipped = await _reconcile_status(session, order, ext_order, is_new)
                if just_shipped:
                    shipped_count += 1
                order_ids.append(order.id)

            if raw_external_orders:
                # Advance the watermark to the newest last_modified actually seen — across
                # every order the platform returned, including ones _drop_unknown_orders_
                # predating_cutoff just excluded from this batch, since those still need to
                # age out of future fetches too. Stamping wall-clock "now" here instead (the
                # previous behavior) would push the watermark past any receipt whose
                # last_modified never changes again, permanently hiding it from every later
                # fetch_orders_since(min_last_modified=...) call — exactly the state a
                # not-yet-reconciled order (e.g. one that arrived already shipped, see
                # _reconcile_status) needs a future sync to revisit. The +1s past the max
                # keeps that same boundary receipt from being re-fetched every single sync.
                newest_modified = max(o.last_modified for o in raw_external_orders)
                connection.last_orders_synced_at = newest_modified + timedelta(seconds=1)

            # Recomputed from scratch every run, not accumulated — an order that has since
            # been paid (or has vanished from the window) simply stops appearing in
            # `skipped_unpaid`, and the hold releases itself with no cleanup pass.
            connection.unpaid_hold_since = _compute_unpaid_hold(skipped_unpaid)

            run = PlatformSyncRun(
                platform=platform,
                mode=SyncRunMode.commit,
                status=SyncRunStatus.success,
                fetched_count=len(external_orders),
                new_count=created_count,
                needs_mapping_count=needs_mapping_count,
                shipped_count=shipped_count,
                skipped_unpaid_count=len(skipped_unpaid),
                finished_at=datetime.now(timezone.utc),
            )
            session.add(run)
            await session.commit()

            return SyncCommitResult(
                fetched_count=len(external_orders),
                created_count=created_count,
                updated_count=updated_count,
                needs_mapping_count=needs_mapping_count,
                shipped_count=shipped_count,
                skipped_unpaid_count=len(skipped_unpaid),
                order_ids=order_ids,
            )
    except Exception as e:
        # A fresh session, not one of the two above — either of those may already be
        # closed (fetch phase) or mid-rollback (write phase) by the time we get here, and
        # _record_failure is a fully self-contained write (add + commit a log row), so it
        # doesn't need to share a session with whatever failed. Matches the pattern
        # sync_scheduler's own failure-recording helpers already use.
        async with async_session_factory() as failure_session:
            await _record_failure(failure_session, platform, SyncRunMode.commit, e)
        raise


def _apply_financials(order: Order, ext_order: ExternalOrder) -> None:
    """Writes the receipt/payment financial fields onto `order` from a freshly-fetched
    ext_order — called both for brand-new orders and, critically, for already-imported
    ones on every later sync, since that's what lets a late change (payment settling, a
    refund) show up without waiting for the order to be re-created. Deliberately touches
    ONLY these financial fields — buyer_name/buyer_note/notes/order_placed_at are
    user-editable via PATCH /orders/{id} and must not be silently overwritten by a sync."""
    order.currency = ext_order.currency
    order.grand_total = _parse_price(ext_order.grand_total)
    order.subtotal = _parse_price(ext_order.subtotal)
    order.shipping_charged = _parse_price(ext_order.shipping_charged)
    order.tax_charged = _parse_price(ext_order.tax_charged)
    order.vat_charged = _parse_price(ext_order.vat_charged)
    order.discount_amount = _parse_price(ext_order.discount_amount)
    order.refunded_amount = _parse_price(ext_order.refunded_amount)

    # Everything above comes straight off the order-list response and is always accurate.
    # The three fields below come from a separate per-order call the adapter may have
    # deliberately skipped (see ExternalOrder.financials_enriched) — writing them anyway
    # would blank out a payment breakdown an earlier sync stored correctly, which is
    # exactly what would happen every time the unpaid hold widened the fetch window.
    if ext_order.financials_enriched:
        order.payment_fees = _parse_price(ext_order.payment_fees)
        order.payment_net = _parse_price(ext_order.payment_net)
        order.payment_status = ext_order.payment_status

    order.financials_synced_at = datetime.now(timezone.utc)


async def _upsert_order(session: AsyncSession, platform: ListingPlatform, ext_order: ExternalOrder) -> tuple[Order, bool]:
    result = await session.execute(
        select(Order).where(Order.platform == platform, Order.external_order_id == ext_order.external_order_id)
    )
    order = result.scalar_one_or_none()
    if order is not None:
        _apply_financials(order, ext_order)
        return order, False

    # Deliberately not persisting ext_order.buyer_name/buyer_note here — see
    # docs/plan-marketplace-integrations.md Section 1e. Nothing in inventory/BOM/
    # allocation logic reads it, and for eBay specifically, never storing a member's
    # name/username is what qualifies the app for eBay's Marketplace Account Deletion
    # exemption instead of having to stand up a public notification endpoint. The
    # buyer_name/buyer_note columns stay for manually-created orders, where the user is
    # typing in their own note, not receiving it via a marketplace API.
    order = Order(
        platform=platform,
        external_order_id=ext_order.external_order_id,
        order_placed_at=ext_order.placed_at,
    )
    _apply_financials(order, ext_order)
    session.add(order)
    await session.flush()
    return order, True


def _parse_price(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


async def _upsert_lines(session: AsyncSession, order: Order, ext_order: ExternalOrder) -> int:
    """Returns how many lines on this order needed mapping. Lines are matched by
    external_line_id and only ever created, never mutated — Etsy line items don't
    change qty/sku after the fact, and re-matching an already-imported line could
    silently reassign stock that's already allocated against it."""
    result = await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))
    existing = {line.external_line_id: line for line in result.scalars()}

    needs_mapping_count = 0
    for ext_line in ext_order.lines:
        if ext_line.external_line_id in existing:
            continue

        product_id, variant_id = await _resolve_line_match(session, order.platform, ext_line.sku)
        needs_mapping = product_id is None and variant_id is None
        if needs_mapping:
            needs_mapping_count += 1

        # cost_per_unit_snapshot is deliberately left NULL here — it's captured at first
        # allocation instead (allocation._allocate_line), once the line is actually going to
        # be fulfilled from real stock at a real cost, rather than off whatever a brand-new/
        # not-yet-costed material's avg_unit_cost happens to be at the instant this line is
        # imported. Packaging cost isn't a line concern at all; it's frozen onto the order's
        # kitting ledger at ship time (kitting.reconcile_order_kitting).
        session.add(
            OrderLine(
                order_id=order.id,
                product_id=product_id,
                variant_id=variant_id,
                ordered_qty=ext_line.qty,
                unit_price=_parse_price(ext_line.unit_price),
                currency=ext_line.currency,
                external_line_id=ext_line.external_line_id,
                sku=ext_line.sku,
                needs_mapping=needs_mapping,
            )
        )
    await default_order_shipping_profile(session, order)
    return needs_mapping_count


async def _reconcile_status(session: AsyncSession, order: Order, ext_order: ExternalOrder, is_new: bool) -> bool:
    """Returns True if this call just marked the order shipped — lets commit_sync report
    a shipped_count so it's visible that already-imported orders are actually being kept
    in sync, not just newly-placed ones."""
    # This function is shared across every platform (order_sync itself is
    # platform-agnostic) — order.platform is always set correctly by _upsert_order by the
    # time this runs, so it's the source of truth for any platform-specific wording below,
    # not a hardcoded name. (Previously hardcoded "Etsy" — confirmed live on a real eBay
    # order surfacing a sync_issue that named the wrong marketplace.)
    platform = order.platform
    # allocation.py's ship_order (and services/returns.process_cancellation, on the
    # cancel side) already clear sync_issue themselves the moment status actually reaches
    # shipped/cancelled — covers both this sync path and a manual cancel/ship action
    # elsewhere. But an order can also already BE in that resolved state by the time a
    # later sync revisits it (e.g. manually allocated and shipped after the flag was set,
    # with nothing left to ship) — those functions won't get called again for it, so
    # self-heal that case here too.
    if order.status in (OrderStatus.shipped, OrderStatus.cancelled) and order.sync_issue is not None:
        order.sync_issue = None

    if ext_order.is_cancelled:
        # Deliberately NOT auto-applied — a marketplace-reported cancellation needs a
        # human to choose a scrap/return-to-stock disposition per line (see
        # services/returns.py), not have it happen silently. Etsy's API has no
        # seller-initiated cancel/refund write endpoint either, so this can only ever
        # flow marketplace -> StockSmith; there's nothing to push back even if we wanted
        # to auto-apply it. See docs/plan-marketplace-integrations.md Section 4.
        if order.status != OrderStatus.cancelled:
            order.pending_marketplace_cancellation = True
        return False

    if ext_order.payment_state is not PaymentState.settled:
        # Two distinct situations land here, both wanting the same outcome — the order
        # exists but reserves nothing, and a human decides what to do with it:
        #
        #  - is_new: first sighting is already refunded (PaymentState.reversed). Unsettled
        #    new orders never get this far; _partition_new_orders drops them entirely.
        #    Importing a reversed one keeps the sales history, and returning before
        #    allocate_order below is what stops it taking stock.
        #  - not is_new: an order that WAS paid has had its payment reversed since import
        #    (chargeback, refund). Its units may be allocated or already shipped.
        #
        # Routed through the existing pending_marketplace_cancellation flag rather than a
        # parallel mechanism, because the stock consequences are identical to a
        # marketplace cancellation: release the reservation, or decide scrap vs
        # return-to-stock on goods already gone. returns.process_cancellation already does
        # exactly that, already clears both fields, and already has a UI. The distinction
        # lives in sync_issue's wording so the user reads "payment reversed" rather than a
        # misleading "cancelled on Etsy".
        if order.status != OrderStatus.cancelled:
            order.pending_marketplace_cancellation = True
            order.sync_issue = (
                f"{_PLATFORM_LABELS[platform]} reports this order's payment as "
                f"{ext_order.payment_state.value} — review and cancel/return it."
            )
        return False

    # Reaching here means the marketplace is currently reporting neither a cancellation
    # nor a payment problem, so any flag raised by an earlier sync no longer reflects
    # reality — clear it. Mirrors the sync_issue self-heal at the top of this function.
    #
    # Not merely tidiness. Both flag-setting branches above are deliberately fail-closed:
    # a missing or unrecognised payment status reads as unsettled, so a partial response
    # or a new enum value can raise this flag on a perfectly good order. Without a way
    # back down that order would stay flagged forever AND — since allocation.
    # auto_allocate_after_build now skips flagged orders — would silently stop receiving
    # stock from builds. A human-cleared-only flag would turn a transient API hiccup into
    # permanent damage.
    if order.pending_marketplace_cancellation:
        order.pending_marketplace_cancellation = False
        order.sync_issue = None

    if is_new:
        # A brand-new order can arrive already shipped on the marketplace's side (seller
        # fulfilled it before this sync ever ran) — allocate first so the is_shipped
        # check right below has something to ship, instead of returning early and losing
        # that signal forever (fetch_orders_since's min_last_modified watermark means a
        # later sync may never see this receipt again if nothing about it changes further).
        await allocation.allocate_order(session, order, source=f"{platform.value}-sync")

    if ext_order.is_shipped and order.status not in (OrderStatus.shipped, OrderStatus.cancelled):
        lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())
        has_allocated = any(line.allocated_qty > line.shipped_qty for line in lines)
        if not has_allocated:
            # This platform's is_shipped can't be reconciled locally — allocation.
            # ship_order would raise "No allocated units to ship" and, since commit_sync
            # is one transaction, take the whole sync batch down with it. Flag it instead
            # so the rest of the batch still commits; self-heals once the order gets
            # allocated (manually, or by a later sync) and actually ships.
            order.sync_issue = (
                f"{_PLATFORM_LABELS[platform]} shows this order as shipped, but no units are allocated "
                "locally — check stock and allocate manually."
            )
            return False
        await allocation.ship_order(session, order)
        return True
    return False


def _describe_error(error: Exception) -> str:
    """A never-empty, human-readable one-liner for the sync log's error_message.

    str() on an exception is not reliably non-empty: httpx's transport errors
    (ConnectTimeout, ReadTimeout) carry their meaning entirely in the class name and
    stringify to "". Storing that verbatim gave the sync panel a run marked "failed" with
    nothing beside it — indistinguishable from a bug in the panel, and leaving the user
    no thread to pull. What it hid most recently was a run of Etsy syncs aborting on a
    connect timeout partway through the per-receipt financial enrichment.

    Deliberately does NOT prefix the class name onto errors that already have a message:
    those are written to be read by the user in the sync panel (PlatformAuthError's
    "reconnect required", the adapters' HTTP-status messages), and "PlatformAuthError: "
    in front of one makes it worse, not clearer.
    """
    message = str(error).strip()
    if message:
        return message[:2000]
    if isinstance(error, httpx.TimeoutException):
        return f"Timed out waiting for the marketplace API ({type(error).__name__})"
    return type(error).__name__


async def _record_failure(session: AsyncSession, platform: ListingPlatform, mode: SyncRunMode, error: Exception) -> None:
    run = PlatformSyncRun(
        platform=platform,
        mode=mode,
        status=SyncRunStatus.error,
        error_message=_describe_error(error),
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()
