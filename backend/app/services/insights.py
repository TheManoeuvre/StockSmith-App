"""Aggregations behind the Insights (MI/BI) page.

Built from the same per-order helpers the Orders page uses, rather than re-deriving profit in
SQL: a second formula would drift from `routers/orders._compute_net_profit` the first time
that one changes, and the owner would be looking at two different "net profit" figures. The
cost is that orders are loaded into Python, which is fine at this business's volume (a few
thousand orders a year) and bounded by MAX_RANGE_DAYS.

Dates are UTC calendar days, matching how timestamps are stored.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.build import Build, BuildFailedConsumption
from app.models.order import Order, OrderLine, OrderStatus
from app.schemas.insights import (
    InsightsBucket,
    InsightsChannelRow,
    InsightsFlaggedOrder,
    InsightsOverview,
    InsightsQuality,
    InsightsTotals,
)
from app.services.kitting import get_kitting_cogs_by_order
from app.services.order_parcels import get_replacement_costs_by_order

MAX_RANGE_DAYS = 800
CHANNELS = ("etsy", "ebay", "shopify", "square", "manual")
GRANULARITIES = ("day", "week", "month")
_CHUNK = 500
ZERO = Decimal(0)
_FLAGGED_LIMIT = 25


@dataclass
class LineFact:
    product_id: int | None
    variant_id: int | None
    units: int
    # unit_price x ordered_qty: the basis for sharing order-level money out across lines.
    value: Decimal
    # Direct costs, known per line. Fees, postage, packaging and resends are order-level.
    materials: Decimal
    failed_build_cost: Decimal


@dataclass
class OrderFact:
    order_id: int
    external_order_id: str | None
    channel: str
    placed_on: date
    units: int
    # None when the order's financials haven't synced — see InsightsTotals.
    revenue: Decimal | None
    gross: Decimal | None
    refunded: Decimal
    platform_fees: Decimal
    postage_cost: Decimal
    materials_cogs: Decimal
    kitting_cogs: Decimal
    replacement_cost: Decimal
    failed_build_cost: Decimal
    net_profit: Decimal | None
    issues: list[str] = field(default_factory=list)
    lines: list[LineFact] = field(default_factory=list)


def channel_of(order: Order) -> str:
    """Platform for synced orders, 'manual' for everything hand-keyed — including a manual
    order tagged with an Etsy/eBay channel, which is still a hand-keyed order (see
    ManualOrderChannel)."""
    return order.platform.value if order.platform is not None else "manual"


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc),
    )


def _bucket_start(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _next_bucket(day: date, granularity: str) -> date:
    if granularity == "week":
        return day + timedelta(days=7)
    if granularity == "month":
        return (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return day + timedelta(days=1)


async def _failed_cost_per_good_unit(session: AsyncSession) -> dict[tuple[int, int | None], Decimal]:
    """Material cost burned on failed builds, per good unit built, per (product, variant).

    Attributed to units SOLD, not to the period the builds happened in: a bad batch in March
    shouldn't make March's sales look unprofitable and April's look great. The ratio uses all
    recorded builds so it is stable, and only counts BOM lines the user marked as consumed
    (BuildFailedConsumption.was_consumed) at the cost frozen when the build was logged.

    A key with failures but no good units built has no usable ratio and is left out."""
    built: dict[tuple[int, int | None], int] = defaultdict(int)
    for row in await session.execute(select(Build.product_id, Build.variant_id, Build.qty_built)):
        built[(row.product_id, row.variant_id)] += row.qty_built

    failed_cost: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: ZERO)
    rows = await session.execute(
        select(
            Build.product_id,
            Build.variant_id,
            BuildFailedConsumption.qty_consumed,
            BuildFailedConsumption.unit_cost_snapshot,
        )
        .join(Build, Build.id == BuildFailedConsumption.build_id)
        .where(BuildFailedConsumption.was_consumed.is_(True), BuildFailedConsumption.unit_cost_snapshot.is_not(None))
    )
    for row in rows:
        failed_cost[(row.product_id, row.variant_id)] += Decimal(str(row.qty_consumed)) * Decimal(
            str(row.unit_cost_snapshot)
        )

    return {key: cost / built[key] for key, cost in failed_cost.items() if built.get(key)}


async def load_order_facts(
    session: AsyncSession, start: date, end: date, channels: set[str] | None = None
) -> list[OrderFact]:
    """One OrderFact per non-cancelled order placed in [start, end]."""
    # Imported here, as notification_summary does: routers.orders pulls in most of the app.
    from app.routers.orders import (
        _cogs_pending,
        _compute_net_profit,
        _effective_postage_cost,
        _materials_cogs,
        _postage_cost_missing,
    )

    lo, hi = _bounds(start, end)
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.lines))
        .where(Order.order_placed_at >= lo, Order.order_placed_at < hi, Order.status != OrderStatus.cancelled)
        .order_by(Order.order_placed_at, Order.id)
    )
    orders = [o for o in result.scalars() if channels is None or channel_of(o) in channels]
    if not orders:
        return []

    ids = [o.id for o in orders]
    kitting: dict[int, Decimal] = {}
    replacement = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        kitting.update(await get_kitting_cogs_by_order(session, chunk))
        replacement.update(await get_replacement_costs_by_order(session, chunk))
    failed_per_unit = await _failed_cost_per_good_unit(session)

    facts: list[OrderFact] = []
    for order in orders:
        repl = replacement.get(order.id)
        kit = kitting.get(order.id)
        materials = _materials_cogs(order)
        profit = _compute_net_profit(order, materials, kit, repl)
        known = order.subtotal is not None

        failed = ZERO
        line_facts: list[LineFact] = []
        for line in order.lines:
            line_failed = ZERO
            if line.shipped_qty > 0 and line.product_id is not None:
                per_unit = failed_per_unit.get((line.product_id, line.variant_id))
                if per_unit is None and line.variant_id is not None:
                    per_unit = failed_per_unit.get((line.product_id, None))
                if per_unit is not None:
                    line_failed = per_unit * line.shipped_qty
            failed += line_failed
            if line.ordered_qty > 0:
                line_facts.append(
                    LineFact(
                        product_id=line.product_id,
                        variant_id=line.variant_id,
                        units=line.ordered_qty,
                        value=Decimal(line.unit_price or 0) * line.ordered_qty,
                        materials=(
                            Decimal(line.cost_per_unit_snapshot) * line.shipped_qty
                            if line.shipped_qty > 0 and line.cost_per_unit_snapshot is not None
                            else ZERO
                        ),
                        failed_build_cost=line_failed,
                    )
                )

        issues: list[str] = []
        if not known:
            issues.append("financials_not_synced")
        if _cogs_pending(order):
            issues.append("cogs_pending")
        if _postage_cost_missing(order, repl):
            issues.append("postage_missing")
        if any(line.needs_mapping for line in order.lines):
            issues.append("unmapped_lines")

        revenue = (
            Decimal(order.subtotal) + Decimal(order.shipping_charged or 0) - Decimal(order.refunded_amount or 0)
            if known
            else None
        )
        facts.append(
            OrderFact(
                order_id=order.id,
                external_order_id=order.external_order_id,
                channel=channel_of(order),
                placed_on=order.order_placed_at.astimezone(timezone.utc).date()
                if order.order_placed_at.tzinfo
                else order.order_placed_at.date(),
                units=sum(line.ordered_qty for line in order.lines),
                revenue=revenue,
                gross=Decimal(order.subtotal) + Decimal(order.shipping_charged or 0) if known else None,
                refunded=Decimal(order.refunded_amount or 0) if known else ZERO,
                platform_fees=Decimal(order.payment_fees or 0) if known else ZERO,
                postage_cost=(_effective_postage_cost(order, repl) or ZERO) if known else ZERO,
                materials_cogs=(materials or ZERO) if known else ZERO,
                kitting_cogs=(kit or ZERO) if known else ZERO,
                replacement_cost=(
                    (repl.parcel_postage + (repl.items_cogs or ZERO)) if repl is not None else ZERO
                )
                if known
                else ZERO,
                failed_build_cost=failed if known else ZERO,
                net_profit=profit,
                issues=issues,
                lines=line_facts,
            )
        )
    return facts


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator * 100).quantize(Decimal("0.1"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def summarise(facts: Sequence[OrderFact], include_failed_builds: bool) -> InsightsTotals:
    known = [f for f in facts if f.net_profit is not None]
    revenue = sum((f.revenue for f in known), ZERO)
    gross = sum((f.gross for f in known), ZERO)
    refunded = sum((f.refunded for f in known), ZERO)
    failed = sum((f.failed_build_cost for f in known), ZERO)
    profit = sum((f.net_profit for f in known), ZERO) - (failed if include_failed_builds else ZERO)
    orders = len(facts)
    return InsightsTotals(
        orders=orders,
        units=sum(f.units for f in facts),
        revenue=_money(revenue),
        platform_fees=_money(sum((f.platform_fees for f in known), ZERO)),
        postage_cost=_money(sum((f.postage_cost for f in known), ZERO)),
        materials_cogs=_money(sum((f.materials_cogs for f in known), ZERO)),
        kitting_cogs=_money(sum((f.kitting_cogs for f in known), ZERO)),
        replacement_cost=_money(sum((f.replacement_cost for f in known), ZERO)),
        refunded=_money(refunded),
        failed_build_cost=_money(failed),
        net_profit=_money(profit),
        margin_percent=_percent(profit, revenue),
        average_order_value=_money(revenue / len(known)) if known else None,
        average_profit_per_order=_money(profit / len(known)) if known else None,
        refund_rate_percent=_percent(refunded, gross),
        profit_unavailable_orders=orders - len(known),
    )


def build_trend(
    facts: Sequence[OrderFact], start: date, end: date, granularity: str, include_failed_builds: bool
) -> list[InsightsBucket]:
    """Zero-filled so a quiet week is a visible dip rather than a missing point."""
    grouped: dict[date, list[OrderFact]] = defaultdict(list)
    for fact in facts:
        grouped[_bucket_start(fact.placed_on, granularity)].append(fact)

    buckets: list[InsightsBucket] = []
    cursor = _bucket_start(start, granularity)
    while cursor <= end:
        totals = summarise(grouped.get(cursor, []), include_failed_builds)
        buckets.append(
            InsightsBucket(
                period_start=cursor,
                orders=totals.orders,
                units=totals.units,
                revenue=totals.revenue,
                net_profit=totals.net_profit,
                margin_percent=totals.margin_percent,
            )
        )
        cursor = _next_bucket(cursor, granularity)
    return buckets


def build_channels(facts: Sequence[OrderFact], include_failed_builds: bool) -> list[InsightsChannelRow]:
    by_channel: dict[str, list[OrderFact]] = defaultdict(list)
    for fact in facts:
        by_channel[fact.channel].append(fact)
    rows = []
    for channel in CHANNELS:
        if channel not in by_channel:
            continue
        totals = summarise(by_channel[channel], include_failed_builds)
        rows.append(
            InsightsChannelRow(
                channel=channel,
                orders=totals.orders,
                units=totals.units,
                revenue=totals.revenue,
                platform_fees=totals.platform_fees,
                net_profit=totals.net_profit,
                margin_percent=totals.margin_percent,
                average_order_value=totals.average_order_value,
            )
        )
    return rows


async def compute_overview(
    session: AsyncSession,
    start: date,
    end: date,
    granularity: str,
    channels: set[str] | None,
    include_failed_builds: bool,
    compare_start: date | None = None,
    compare_end: date | None = None,
) -> InsightsOverview:
    facts = await load_order_facts(session, start, end, channels)
    overview = InsightsOverview(
        start=start,
        end=end,
        granularity=granularity,
        include_failed_builds=include_failed_builds,
        totals=summarise(facts, include_failed_builds),
        trend=build_trend(facts, start, end, granularity, include_failed_builds),
        channels=build_channels(facts, include_failed_builds),
    )
    if compare_start is not None and compare_end is not None:
        compare_facts = await load_order_facts(session, compare_start, compare_end, channels)
        overview.compare_start = compare_start
        overview.compare_end = compare_end
        overview.compare_totals = summarise(compare_facts, include_failed_builds)
        overview.compare_trend = build_trend(compare_facts, compare_start, compare_end, granularity, include_failed_builds)
    return overview


async def compute_quality(
    session: AsyncSession, start: date, end: date, channels: set[str] | None
) -> InsightsQuality:
    facts = await load_order_facts(session, start, end, channels)
    flagged = [f for f in facts if f.issues]

    def count(issue: str) -> int:
        return sum(1 for f in flagged if issue in f.issues)

    return InsightsQuality(
        cogs_pending=count("cogs_pending"),
        postage_missing=count("postage_missing"),
        financials_not_synced=count("financials_not_synced"),
        unmapped_lines=count("unmapped_lines"),
        flagged_total=len(flagged),
        flagged=[
            InsightsFlaggedOrder(
                order_id=f.order_id,
                external_order_id=f.external_order_id,
                channel=f.channel,
                placed_on=f.placed_on,
                issues=f.issues,
            )
            for f in reversed(flagged[-_FLAGGED_LIMIT:])
        ],
    )
