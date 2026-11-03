"""Insights aggregations (services/insights).

The point of building on the Orders page's own helpers is that the two agree, so the first test
pins the arithmetic against a hand-worked order. The rest cover what the aggregation itself
adds: which orders count, how missing financials are kept out of the sums, zero-filled trend
buckets, comparison periods, and failed-build cost attributed to units sold.
"""

import itertools
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.build import Build, BuildFailedConsumption
from app.models.listing import ListingPlatform
from app.models.material import Material, MaterialUnit
from sqlalchemy import select

from app.models.order import Order, OrderLine, OrderStatus
from app.models.order_parcel import OrderReplacementParcel, ReplacementParcelReason
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant
from app.services import insights, insights_breakdown as breakdown, material_categories


_order_numbers = itertools.count(1)


def _at(day: int, month: int = 3) -> datetime:
    return datetime(2026, month, day, 10, 0, tzinfo=timezone.utc)


async def _product(session, sku: str = "P1") -> Product:
    product = Product(name=f"Product {sku}", sku=sku)
    session.add(product)
    await session.flush()
    return product


async def _order(
    session,
    product: Product | None,
    *,
    placed: datetime,
    platform: ListingPlatform | None = ListingPlatform.etsy,
    status: OrderStatus = OrderStatus.shipped,
    subtotal: str | None = "20.00",
    shipping: str = "3.00",
    fees: str = "2.00",
    refunded: str | None = None,
    qty: int = 2,
    unit_cost: str | None = "4.00",
    postage_snapshot: str | None = "3.65",
    needs_mapping: bool = False,
) -> Order:
    shipped = qty if status == OrderStatus.shipped else 0
    order = Order(
        platform=platform,
        external_order_id=f"X{next(_order_numbers)}",
        status=status,
        order_placed_at=placed,
        subtotal=Decimal(subtotal) if subtotal is not None else None,
        shipping_charged=Decimal(shipping),
        payment_fees=Decimal(fees),
        refunded_amount=Decimal(refunded) if refunded else None,
        shipping_cost_snapshot=Decimal(postage_snapshot) if postage_snapshot is not None else None,
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderLine(
            order_id=order.id,
            product_id=None if needs_mapping else product.id,
            ordered_qty=qty,
            allocated_qty=shipped,
            shipped_qty=shipped,
            cost_per_unit_snapshot=Decimal(unit_cost) if unit_cost is not None else None,
            needs_mapping=needs_mapping,
        )
    )
    await session.flush()
    return order


async def _overview(session, start=date(2026, 3, 1), end=date(2026, 3, 31), **kwargs):
    kwargs.setdefault("granularity", "week")
    kwargs.setdefault("channels", None)
    kwargs.setdefault("include_failed_builds", True)
    return await insights.compute_overview(session, start, end, **kwargs)


async def test_totals_match_the_orders_page_arithmetic(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))

    totals = (await _overview(session)).totals

    # revenue 20 + 3 = 23; profit 23 - fees 2 - postage 3.65 - materials 2 x 4 = 9.35
    assert totals.orders == 1
    assert totals.units == 2
    assert totals.revenue == Decimal("23.00")
    assert totals.platform_fees == Decimal("2.00")
    assert totals.postage_cost == Decimal("3.65")
    assert totals.materials_cogs == Decimal("8.00")
    assert totals.net_profit == Decimal("9.35")
    assert totals.margin_percent == Decimal("40.7")
    assert totals.average_order_value == Decimal("23.00")


async def test_orders_without_synced_financials_are_flagged_not_counted_as_zero(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))
    await _order(session, product, placed=_at(6), subtotal=None)

    totals = (await _overview(session)).totals

    assert totals.orders == 2
    assert totals.profit_unavailable_orders == 1
    assert totals.revenue == Decimal("23.00")
    assert totals.net_profit == Decimal("9.35")
    # Averages are over the orders whose figures are known, not diluted by the unknown one.
    assert totals.average_order_value == Decimal("23.00")


async def test_cancelled_orders_and_orders_outside_the_range_are_ignored(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))
    await _order(session, product, placed=_at(6), status=OrderStatus.cancelled)
    await _order(session, product, placed=_at(1, month=2))
    await _order(session, product, placed=datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc))
    await _order(session, product, placed=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc))

    assert (await _overview(session)).totals.orders == 2


async def test_channel_filter_and_manual_bucket(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5), platform=ListingPlatform.etsy)
    await _order(session, product, placed=_at(6), platform=ListingPlatform.ebay)
    await _order(session, product, placed=_at(7), platform=None)

    overview = await _overview(session)
    assert {row.channel: row.orders for row in overview.channels} == {"etsy": 1, "ebay": 1, "manual": 1}

    filtered = await _overview(session, channels={"etsy", "manual"})
    assert filtered.totals.orders == 2
    assert {row.channel for row in filtered.channels} == {"etsy", "manual"}


async def test_trend_is_zero_filled_and_week_starts_on_monday(session):
    product = await _product(session)
    # Mon 2 Mar and Mon 16 Mar: the week of the 9th has nothing in it.
    await _order(session, product, placed=_at(4))
    await _order(session, product, placed=_at(17))

    trend = (await _overview(session, start=date(2026, 3, 2), end=date(2026, 3, 22))).trend

    assert [b.period_start for b in trend] == [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16)]
    assert [b.orders for b in trend] == [1, 0, 1]
    assert trend[1].revenue == Decimal("0.00")
    assert trend[1].margin_percent is None


async def test_monthly_trend_spans_every_month_in_range(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5, month=1))
    await _order(session, product, placed=_at(5, month=3))

    trend = (await _overview(session, start=date(2026, 1, 1), end=date(2026, 3, 31), granularity="month")).trend

    assert [(b.period_start, b.orders) for b in trend] == [
        (date(2026, 1, 1), 1),
        (date(2026, 2, 1), 0),
        (date(2026, 3, 1), 1),
    ]


async def test_comparison_period_is_summarised_separately(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))
    await _order(session, product, placed=_at(5, month=2))
    await _order(session, product, placed=_at(6, month=2))

    overview = await _overview(session, compare_start=date(2026, 2, 1), compare_end=date(2026, 2, 28))

    assert overview.totals.orders == 1
    assert overview.compare_totals.orders == 2
    assert overview.compare_trend is not None and sum(b.orders for b in overview.compare_trend) == 2


async def test_failed_build_cost_is_attributed_to_units_sold(session):
    product = await _product(session)
    category = await material_categories.find_or_create(session, "filament")
    material = Material(
        name="Filament",
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.g,
    )
    session.add(material)
    await session.flush()

    # 8 good units built and 2 failures that burned 10 x £1.00 of filament = £1.25 per good unit.
    build = Build(product_id=product.id, qty_built=8, qty_failed=2)
    session.add(build)
    await session.flush()
    session.add(
        BuildFailedConsumption(
            build_id=build.id,
            material_id=material.id,
            was_consumed=True,
            qty_consumed=Decimal(10),
            unit_cost_snapshot=Decimal("1.00"),
        )
    )
    await _order(session, product, placed=_at(5), qty=2)

    with_failed = (await _overview(session)).totals
    without = (await _overview(session, include_failed_builds=False)).totals

    assert with_failed.failed_build_cost == Decimal("2.50")
    assert without.failed_build_cost == Decimal("2.50")
    assert without.net_profit == Decimal("9.35")
    assert with_failed.net_profit == Decimal("6.85")


async def test_quality_counts_each_issue_and_lists_flagged_orders(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))
    pending = await _order(session, product, placed=_at(6), unit_cost=None)
    no_postage = await _order(session, product, placed=_at(7), postage_snapshot=None)
    unsynced = await _order(session, product, placed=_at(8), subtotal=None)
    unmapped = await _order(session, None, placed=_at(9), needs_mapping=True)

    quality = await insights.compute_quality(session, date(2026, 3, 1), date(2026, 3, 31), None)

    assert quality.cogs_pending == 1
    assert quality.postage_missing == 1
    assert quality.financials_not_synced == 1
    assert quality.unmapped_lines == 1
    assert quality.flagged_total == 4
    assert {f.order_id for f in quality.flagged} == {pending.id, no_postage.id, unsynced.id, unmapped.id}
    # Newest first, so the most recent problems are on top.
    assert quality.flagged[0].order_id == unmapped.id


@pytest.mark.parametrize("granularity", ["day", "week", "month"])
async def test_empty_range_returns_zeroes_not_errors(session, granularity):
    overview = await _overview(session, granularity=granularity)

    assert overview.totals.orders == 0
    assert overview.totals.net_profit == Decimal("0.00")
    assert overview.totals.margin_percent is None
    assert overview.channels == []
    assert len(overview.trend) >= 1


# --- Products and costs -----------------------------------------------------------------


async def _two_line_order(session, cheap: Product, dear: Product) -> Order:
    """£10 and £30 of items, £4 postage charged, £4 fees, £3.65 postage cost: so the cheap line
    carries a quarter of everything order-level."""
    order = Order(
        platform=ListingPlatform.etsy,
        external_order_id=f"X{next(_order_numbers)}",
        status=OrderStatus.shipped,
        order_placed_at=_at(5),
        subtotal=Decimal("40.00"),
        shipping_charged=Decimal("4.00"),
        payment_fees=Decimal("4.00"),
        shipping_cost_snapshot=Decimal("3.65"),
    )
    session.add(order)
    await session.flush()
    for product, price, cost in ((cheap, "10.00", "2.00"), (dear, "30.00", "6.00")):
        session.add(
            OrderLine(
                order_id=order.id,
                product_id=product.id,
                ordered_qty=1,
                allocated_qty=1,
                shipped_qty=1,
                unit_price=Decimal(price),
                cost_per_unit_snapshot=Decimal(cost),
            )
        )
    await session.flush()
    return order


async def _products(session, **kwargs):
    kwargs.setdefault("view", "products")
    kwargs.setdefault("include_failed_builds", True)
    return await breakdown.compute_products(session, date(2026, 3, 1), date(2026, 3, 31), None, **kwargs)


async def _filament(session) -> Material:
    category = await material_categories.find_or_create(session, "filament")
    material = Material(
        name="Filament",
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.g,
    )
    session.add(material)
    await session.flush()
    return material


async def test_product_rows_share_order_money_by_line_value_and_add_up_to_the_order(session):
    cheap, dear = await _product(session, "CHEAP"), await _product(session, "DEAR")
    await _two_line_order(session, cheap, dear)

    result = await _products(session)
    rows = {r.name: r for r in result.rows}

    # Order revenue 44, fees 4, postage 3.65, materials 2 + 6: profit 44 - 4 - 3.65 - 8 = 28.35.
    assert result.totals.net_profit == Decimal("28.35")
    assert rows["Product CHEAP"].revenue == Decimal("11.00")
    # The cheap line is a quarter: 11 - 1 fees - 0.9125 postage - 2 materials = 7.0875.
    assert rows["Product CHEAP"].net_profit == Decimal("7.09")
    assert rows["Product DEAR"].net_profit == Decimal("21.26")
    assert sum(r.net_profit for r in rows.values()) == result.totals.net_profit
    assert sum(r.share_of_profit_percent for r in rows.values()) == Decimal("100.0")


async def test_product_rows_sort_and_paginate_on_the_server(session):
    cheap, dear = await _product(session, "CHEAP"), await _product(session, "DEAR")
    await _two_line_order(session, cheap, dear)

    first = await _products(session, page_size=1)
    assert [r.name for r in first.rows] == ["Product DEAR"]
    assert first.total_rows == 2

    second = await _products(session, page_size=1, page=2)
    assert [r.name for r in second.rows] == ["Product CHEAP"]

    ascending = await _products(session, sort="name", descending=False)
    assert [r.name for r in ascending.rows] == ["Product CHEAP", "Product DEAR"]

    searched = await _products(session, q="dear")
    assert [r.name for r in searched.rows] == ["Product DEAR"]


async def test_unmapped_lines_are_counted_in_units_but_not_given_a_product(session):
    product = await _product(session)
    await _order(session, product, placed=_at(5))
    await _order(session, None, placed=_at(6), needs_mapping=True, qty=3)

    result = await _products(session)

    assert result.unmapped_units == 3
    assert [r.units for r in result.rows] == [2]


async def test_variants_view_splits_a_product_and_categories_view_groups_it(session):
    product = await _product(session)
    variant = ProductVariant(product_id=product.id, variant_name="Large", attribute1_value="L")
    session.add(variant)
    await session.flush()
    order = await _order(session, product, placed=_at(5))
    line = (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalar_one()
    line.variant_id = variant.id
    await session.flush()

    variants = await _products(session, view="variants")
    assert [(r.name, r.sub) for r in variants.rows] == [("Large", "Product P1 · L")]

    categories = await _products(session, view="categories")
    assert [r.name for r in categories.rows] == ["Uncategorised"]


async def test_failed_builds_lower_a_products_margin_and_report_its_fail_rate(session):
    product = await _product(session)
    material = await _filament(session)
    build = Build(product_id=product.id, qty_built=8, qty_failed=2)
    session.add(build)
    await session.flush()
    session.add(
        BuildFailedConsumption(
            build_id=build.id,
            material_id=material.id,
            was_consumed=True,
            qty_consumed=Decimal(10),
            unit_cost_snapshot=Decimal("1.00"),
        )
    )
    await _order(session, product, placed=_at(5), qty=2)

    row = (await _products(session)).rows[0]

    assert row.fail_rate_percent == Decimal("20.0")
    assert row.failed_build_cost == Decimal("2.50")
    assert row.net_profit == Decimal("6.85")
    assert row.net_profit_before_failed_builds == Decimal("9.35")
    assert row.margin_before_failed_builds_percent > row.margin_percent

    ignored = (await _products(session, include_failed_builds=False)).rows[0]
    assert ignored.net_profit == Decimal("9.35")


async def test_costs_waterfall_reconciles_to_net_profit(session):
    cheap, dear = await _product(session, "CHEAP"), await _product(session, "DEAR")
    order = await _two_line_order(session, cheap, dear)
    session.add(
        OrderReplacementParcel(
            order_id=order.id, reason=ReplacementParcelReason.faulty_item, postage_cost=Decimal("2.50")
        )
    )
    await session.flush()

    costs = await breakdown.compute_costs(session, date(2026, 3, 1), date(2026, 3, 31), None, True)

    amounts = {c.key: c.amount for c in costs.waterfall}
    assert amounts["gross"] == Decimal("44.00")
    assert amounts["resends"] == Decimal("-2.50")
    assert sum(a for k, a in amounts.items() if k != "net_profit") == amounts["net_profit"]
    assert [(r.key, r.amount) for r in costs.resends_by_reason] == [("faulty_item", Decimal("2.50"))]
    assert costs.channels[0].channel == "etsy"
    assert costs.channels[0].net_profit == amounts["net_profit"]


async def test_costs_split_materials_by_category_using_the_current_bill_of_materials(session):
    product = await _product(session)
    for name, category_name, cost in (("Filament", "filament", "1.00"), ("Box", "packaging", "3.00")):
        category = await material_categories.find_or_create(session, category_name)
        material = Material(
            name=name,
            category=material_categories.legacy_value_for(category.name),
            category_id=category.id,
            unit=MaterialUnit.g,
            avg_unit_cost=Decimal(cost),
        )
        session.add(material)
        await session.flush()
        session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal(1)))
    await session.flush()
    await _order(session, product, placed=_at(5))  # materials £8 on the order

    costs = await breakdown.compute_costs(session, date(2026, 3, 1), date(2026, 3, 31), None, True)

    assert {r.key: r.amount for r in costs.materials_by_category} == {
        "packaging": Decimal("6.00"),
        "filament": Decimal("2.00"),
    }


async def test_resend_labels_nobody_has_linked_still_show_up_in_the_reasons(session):
    rows = await breakdown._resends_by_reason(session, [], Decimal("4.00"))

    assert [(r.key, r.amount) for r in rows] == [("unspecified", Decimal("4.00"))]
