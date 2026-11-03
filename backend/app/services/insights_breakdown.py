"""Product/variant/category rows and the cost breakdown for the Insights page.

Builds on the per-order facts in services/insights. Order-level money (revenue including
postage charged, platform fees, postage cost, packaging, resends) is shared out across an
order's lines by line value, so the rows add back up to the order's own net profit and the
product table reconciles with the headline figure.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.build import Build
from app.schemas.insights import (
    InsightsAmountRow,
    InsightsChannelCosts,
    InsightsCostComponent,
    InsightsCosts,
    InsightsProductRow,
    InsightsProducts,
)
from app.services.insights import (
    CHANNELS,
    ZERO,
    _CHUNK,
    LineFact,
    OrderFact,
    _bucket_start,
    _money,
    _next_bucket,
    _percent,
    load_order_facts,
    summarise,
)

SORT_KEYS = (
    "name",
    "units",
    "revenue",
    "cogs",
    "net_profit",
    "margin_percent",
    "profit_per_unit",
    "fail_rate_percent",
    "share_of_profit_percent",
)
VIEWS = ("products", "variants", "categories")


@dataclass
class _Acc:
    key: str
    product_id: int | None = None
    variant_id: int | None = None
    category_id: int | None = None
    units: int = 0
    revenue: Decimal = ZERO
    cogs: Decimal = ZERO
    failed: Decimal = ZERO
    profit: Decimal = ZERO  # after failed builds; the "before" figure is profit + failed
    channel_units: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    trend: dict[date, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    trend_failed: dict[date, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))


def _shares(lines: Sequence[LineFact]) -> list[Decimal]:
    """Each line's share of its order's money: by line value, or by units when no line has a
    price (a manual order keyed without unit prices)."""
    total = sum((line.value for line in lines), ZERO)
    if total > 0:
        return [line.value / total for line in lines]
    units = sum(line.units for line in lines)
    return [Decimal(line.units) / units for line in lines] if units else [ZERO for _ in lines]


def _accumulate(facts: Sequence[OrderFact], granularity: str, key_of) -> tuple[dict[str, _Acc], int, int]:
    """Groups lines by key_of(line). Returns (groups, unmapped_units, unavailable_units)."""
    groups: dict[str, _Acc] = {}
    unmapped_units = 0
    unavailable_units = 0
    for fact in facts:
        shares = _shares(fact.lines)
        bucket = _bucket_start(fact.placed_on, granularity)
        for line, share in zip(fact.lines, shares):
            if line.product_id is None:
                unmapped_units += line.units
                continue
            key, ids = key_of(line)
            acc = groups.setdefault(key, _Acc(key=key, **ids))
            acc.units += line.units
            acc.channel_units[fact.channel] += line.units
            if fact.net_profit is None:
                unavailable_units += line.units
                continue
            cogs = line.materials + fact.kitting_cogs * share
            profit = (
                (fact.revenue - fact.platform_fees - fact.postage_cost - fact.replacement_cost) * share
                - cogs
                - line.failed_build_cost
            )
            acc.revenue += fact.revenue * share
            acc.cogs += cogs
            acc.failed += line.failed_build_cost
            acc.profit += profit
            acc.trend[bucket] += profit
            acc.trend_failed[bucket] += line.failed_build_cost
    return groups, unmapped_units, unavailable_units


async def _build_counts(session: AsyncSession) -> dict[tuple[int, int | None], tuple[int, int]]:
    """(good, failed) units built per (product, variant), all-time."""
    counts: dict[tuple[int, int | None], list[int]] = defaultdict(lambda: [0, 0])
    for row in await session.execute(select(Build.product_id, Build.variant_id, Build.qty_built, Build.qty_failed)):
        counts[(row.product_id, row.variant_id)][0] += row.qty_built
        counts[(row.product_id, row.variant_id)][1] += row.qty_failed
    return {k: (v[0], v[1]) for k, v in counts.items()}


def _fail_rate(good: int, failed: int) -> Decimal | None:
    total = good + failed
    return _percent(Decimal(failed), Decimal(total)) if total else None


async def compute_products(
    session: AsyncSession,
    start: date,
    end: date,
    channels: set[str] | None,
    view: str,
    include_failed_builds: bool,
    granularity: str = "week",
    q: str | None = None,
    sort: str = "net_profit",
    descending: bool = True,
    page: int = 1,
    page_size: int = 10,
    product_id: int | None = None,
    facts: Sequence[OrderFact] | None = None,
) -> InsightsProducts:
    from app.models.product import Product
    from app.models.product_category import ProductCategory
    from app.models.variant import ProductVariant

    if facts is None:
        facts = await load_order_facts(session, start, end, channels)

    products = {p.id: p for p in (await session.execute(select(Product))).scalars()}
    categories = {c.id: c.name for c in (await session.execute(select(ProductCategory))).scalars()}
    variants = {v.id: v for v in (await session.execute(select(ProductVariant))).scalars()}

    def key_of(line: LineFact):
        if view == "variants":
            if line.variant_id is not None:
                return f"v{line.variant_id}", {"product_id": line.product_id, "variant_id": line.variant_id}
            return f"p{line.product_id}", {"product_id": line.product_id}
        if view == "categories":
            product = products.get(line.product_id)
            category_id = product.product_category_id if product is not None else None
            return f"c{category_id or 0}", {"category_id": category_id}
        return f"p{line.product_id}", {"product_id": line.product_id}

    groups, unmapped_units, unavailable_units = _accumulate(facts, granularity, key_of)
    if product_id is not None:
        groups = {k: g for k, g in groups.items() if g.product_id == product_id}

    counts = await _build_counts(session)
    buckets: list[date] = []
    cursor = _bucket_start(start, granularity)
    while cursor <= end:
        buckets.append(cursor)
        cursor = _next_bucket(cursor, granularity)

    def fail_rate(acc: _Acc) -> Decimal | None:
        if view == "categories":
            good = failed = 0
            for (pid, _vid), (g, f) in counts.items():
                product = products.get(pid)
                if product is not None and product.product_category_id == acc.category_id:
                    good, failed = good + g, failed + f
            return _fail_rate(good, failed)
        if acc.variant_id is not None:
            return _fail_rate(*counts[(acc.product_id, acc.variant_id)]) if (acc.product_id, acc.variant_id) in counts else None
        good = failed = 0
        for (pid, vid), (g, f) in counts.items():
            # A product row covers its variants' builds too; a variants-view base row only its own.
            if pid == acc.product_id and (view == "products" or vid is None):
                good, failed = good + g, failed + f
        return _fail_rate(good, failed)

    def describe(acc: _Acc) -> tuple[str, str]:
        if view == "categories":
            return categories.get(acc.category_id, "Uncategorised"), "category"
        product = products.get(acc.product_id)
        product_name = product.name if product is not None else f"Product {acc.product_id}"
        if view == "variants":
            if acc.variant_id is None:
                return "Base product", product_name
            variant = variants.get(acc.variant_id)
            if variant is None:
                return f"Variant {acc.variant_id}", product_name
            attrs = " · ".join(
                v for v in (variant.attribute1_value, variant.attribute2_value, variant.attribute3_value) if v
            )
            return variant.variant_name, product_name + (f" · {attrs}" if attrs else "")
        return product_name, (product.sku or "") if product is not None else ""

    def shown(acc: _Acc) -> Decimal:
        return acc.profit if include_failed_builds else acc.profit + acc.failed

    total_profit = sum((shown(g) for g in groups.values()), ZERO)
    rows: list[InsightsProductRow] = []
    for acc in groups.values():
        name, sub = describe(acc)
        if q and q.strip().lower() not in f"{name} {sub}".lower():
            continue
        before = acc.profit + acc.failed
        profit = shown(acc)
        rows.append(
            InsightsProductRow(
                key=acc.key,
                name=name,
                sub=sub,
                product_id=acc.product_id,
                variant_id=acc.variant_id,
                units=acc.units,
                revenue=_money(acc.revenue),
                cogs=_money(acc.cogs),
                failed_build_cost=_money(acc.failed),
                net_profit=_money(profit),
                net_profit_before_failed_builds=_money(before),
                margin_percent=_percent(profit, acc.revenue),
                margin_before_failed_builds_percent=_percent(before, acc.revenue),
                profit_per_unit=_money(profit / acc.units) if acc.units else None,
                share_of_profit_percent=_percent(profit, total_profit),
                fail_rate_percent=fail_rate(acc),
                channel_units=dict(acc.channel_units),
                trend=[
                    _money(acc.trend.get(b, ZERO) + (ZERO if include_failed_builds else acc.trend_failed.get(b, ZERO)))
                    for b in buckets
                ],
            )
        )

    if sort == "name":
        rows.sort(key=lambda r: r.name.lower(), reverse=descending)
    else:
        present = [r for r in rows if getattr(r, sort) is not None]
        absent = [r for r in rows if getattr(r, sort) is None]
        present.sort(key=lambda r: getattr(r, sort), reverse=descending)
        rows = present + absent

    page = max(1, page)
    page_size = max(1, page_size)
    offset = (page - 1) * page_size
    return InsightsProducts(
        view=view,
        rows=rows[offset : offset + page_size],
        total_rows=len(rows),
        page=page,
        page_size=page_size,
        unmapped_units=unmapped_units,
        profit_unavailable_units=unavailable_units,
        totals=summarise(facts, include_failed_builds),
    )


# --- Costs ------------------------------------------------------------------------------


async def _material_category_shares(
    session: AsyncSession, keys: set[tuple[int, int | None]]
) -> dict[tuple[int, int | None], dict[str, Decimal]]:
    """For each (product, variant): the fraction of its CURRENT bill-of-materials cost that
    falls in each material category. Only the split is used; amounts come from the cost
    frozen on each order line, so historical totals still reconcile with the Orders page."""
    from app.models.material import Material
    from app.models.product import ProductMaterial
    from app.services.buildability import get_resolved_variant_bom

    materials = {
        m.id: (Decimal(m.avg_unit_cost or 0), m.category_name)
        for m in (await session.execute(select(Material))).scalars()
    }
    base: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for pm in (await session.execute(select(ProductMaterial))).scalars():
        cost, category = materials.get(pm.material_id, (ZERO, "other"))
        base[pm.product_id][category] += Decimal(pm.qty_required) * cost

    def normalise(split: dict[str, Decimal]) -> dict[str, Decimal]:
        total = sum(split.values(), ZERO)
        return {k: v / total for k, v in split.items()} if total > 0 else {}

    shares: dict[tuple[int, int | None], dict[str, Decimal]] = {}
    for product_id, variant_id in keys:
        split: dict[str, Decimal] = defaultdict(lambda: ZERO)
        if variant_id is not None:
            for line in await get_resolved_variant_bom(session, product_id, variant_id):
                cost, category = materials.get(line.material_id, (ZERO, "other"))
                split[category] += line.qty_required * cost
        if not split:
            split = base.get(product_id, {})
        shares[(product_id, variant_id)] = normalise(dict(split))
    return shares


async def _resends_by_reason(
    session: AsyncSession, order_ids: Sequence[int], total_resend_cost: Decimal
) -> list[InsightsAmountRow]:
    from app.models.order_parcel import OrderReplacementParcel

    by_reason: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for i in range(0, len(order_ids), _CHUNK):
        result = await session.execute(
            select(OrderReplacementParcel)
            .options(selectinload(OrderReplacementParcel.items), selectinload(OrderReplacementParcel.charge))
            .where(OrderReplacementParcel.order_id.in_(list(order_ids[i : i + _CHUNK])))
        )
        for parcel in result.scalars():
            postage = (
                Decimal(parcel.charge.amount)
                if parcel.charge is not None and parcel.charge.amount is not None
                else Decimal(parcel.postage_cost or 0)
            )
            items = sum(
                (
                    Decimal(it.qty) * Decimal(it.unit_cost_snapshot)
                    for it in parcel.items
                    if it.unit_cost_snapshot is not None
                ),
                ZERO,
            )
            by_reason[parcel.reason.value] += postage + items

    # Resend labels nobody has linked to a parcel still cost money (see ReplacementCosts), so
    # whatever the parcels don't explain is shown rather than hidden.
    explained = sum(by_reason.values(), ZERO)
    if total_resend_cost - explained > Decimal("0.005"):
        by_reason["unspecified"] += total_resend_cost - explained

    labels = {
        "faulty_item": "Faulty item",
        "missing_from_order": "Missing from order",
        "lost_in_transit": "Lost in transit",
        "damaged_in_transit": "Damaged in transit",
        "other": "Other",
        "unspecified": "Reason not recorded",
    }
    return [
        InsightsAmountRow(key=k, label=labels.get(k, k), amount=_money(v))
        for k, v in sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True)
        if v > 0
    ]


async def compute_costs(
    session: AsyncSession, start: date, end: date, channels: set[str] | None, include_failed_builds: bool
) -> InsightsCosts:
    facts = await load_order_facts(session, start, end, channels)
    totals = summarise(facts, include_failed_builds)

    waterfall = [
        InsightsCostComponent(key="gross", label="Gross takings", amount=_money(totals.revenue + totals.refunded)),
        InsightsCostComponent(key="refunds", label="Refunds", amount=-totals.refunded),
        InsightsCostComponent(key="fees", label="Platform fees", amount=-totals.platform_fees),
        InsightsCostComponent(key="postage", label="Postage", amount=-totals.postage_cost),
        InsightsCostComponent(key="materials", label="Materials", amount=-totals.materials_cogs),
        InsightsCostComponent(key="packaging", label="Packaging", amount=-totals.kitting_cogs),
        InsightsCostComponent(key="resends", label="Resends", amount=-totals.replacement_cost),
    ]
    if include_failed_builds:
        waterfall.append(InsightsCostComponent(key="failed_builds", label="Failed builds", amount=-totals.failed_build_cost))
    waterfall.append(InsightsCostComponent(key="net_profit", label="Net profit", amount=totals.net_profit))

    by_channel: dict[str, list[OrderFact]] = defaultdict(list)
    for fact in facts:
        by_channel[fact.channel].append(fact)
    channel_rows = []
    for channel in CHANNELS:
        if channel not in by_channel:
            continue
        t = summarise(by_channel[channel], include_failed_builds)
        channel_rows.append(
            InsightsChannelCosts(
                channel=channel,
                revenue=t.revenue,
                fees=t.platform_fees,
                postage=t.postage_cost,
                materials=t.materials_cogs,
                packaging=t.kitting_cogs,
                resends=t.replacement_cost,
                failed_builds=t.failed_build_cost if include_failed_builds else ZERO,
                net_profit=t.net_profit,
            )
        )

    known = [f for f in facts if f.net_profit is not None]
    keys = {
        (line.product_id, line.variant_id)
        for fact in known
        for line in fact.lines
        if line.product_id is not None and line.materials > 0
    }
    shares = await _material_category_shares(session, keys)
    by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for fact in known:
        for line in fact.lines:
            if line.product_id is None or line.materials <= 0:
                continue
            split = shares.get((line.product_id, line.variant_id)) or {"other": Decimal(1)}
            for category, fraction in split.items():
                by_category[category] += line.materials * fraction

    products = await compute_products(
        session, start, end, channels, "products", include_failed_builds, page_size=100000, facts=facts
    )
    wasted = sorted((r for r in products.rows if r.failed_build_cost > 0), key=lambda r: r.failed_build_cost, reverse=True)

    return InsightsCosts(
        include_failed_builds=include_failed_builds,
        waterfall=waterfall,
        channels=channel_rows,
        materials_by_category=[
            InsightsAmountRow(key=k, label=k.replace("_", " ").capitalize(), amount=_money(v))
            for k, v in sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
        ],
        resends_by_reason=await _resends_by_reason(session, [f.order_id for f in known], totals.replacement_cost),
        failed_builds=wasted[:8],
        failed_builds_total=totals.failed_build_cost,
        failed_builds_percent_of_materials=_percent(
            totals.failed_build_cost, totals.materials_cogs + totals.failed_build_cost
        ),
    )
