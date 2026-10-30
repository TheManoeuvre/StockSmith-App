from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class InsightsTotals(BaseModel):
    """Headline figures for one period. Every money figure is built from the same helpers the
    Orders page uses (routers/orders._compute_net_profit and friends), so the two never
    disagree.

    Orders whose financials haven't synced (subtotal unknown) count towards `orders` and
    `units` but contribute nothing to revenue, costs or profit — they are reported in
    `profit_unavailable_orders` instead of being treated as zero. `margin_percent` is
    therefore profit over the revenue of orders whose profit IS known."""

    orders: int
    units: int
    revenue: Decimal
    platform_fees: Decimal
    postage_cost: Decimal
    materials_cogs: Decimal
    kitting_cogs: Decimal
    replacement_cost: Decimal
    refunded: Decimal
    # Failed-build cost attributed to the units sold in this period (not the period the builds
    # happened in) — see services/insights._failed_cost_per_good_unit.
    failed_build_cost: Decimal
    # After failed_build_cost when the request asked for it (include_failed_builds).
    net_profit: Decimal
    margin_percent: Decimal | None
    average_order_value: Decimal | None
    average_profit_per_order: Decimal | None
    refund_rate_percent: Decimal | None
    profit_unavailable_orders: int


class InsightsBucket(BaseModel):
    period_start: date
    orders: int
    units: int
    revenue: Decimal
    net_profit: Decimal
    margin_percent: Decimal | None


class InsightsChannelRow(BaseModel):
    channel: str
    orders: int
    units: int
    revenue: Decimal
    platform_fees: Decimal
    net_profit: Decimal
    margin_percent: Decimal | None
    average_order_value: Decimal | None


class InsightsOverview(BaseModel):
    start: date
    end: date
    granularity: str
    include_failed_builds: bool
    totals: InsightsTotals
    trend: list[InsightsBucket]
    channels: list[InsightsChannelRow]
    compare_start: date | None = None
    compare_end: date | None = None
    compare_totals: InsightsTotals | None = None
    compare_trend: list[InsightsBucket] | None = None


class InsightsFlaggedOrder(BaseModel):
    order_id: int
    external_order_id: str | None
    channel: str
    placed_on: date
    issues: list[str]


class InsightsQuality(BaseModel):
    cogs_pending: int
    postage_missing: int
    financials_not_synced: int
    unmapped_lines: int
    flagged_total: int
    flagged: list[InsightsFlaggedOrder]


class InsightsProductRow(BaseModel):
    # Stable key for the row: "p12" (product), "v34" (variant), "c5" (category).
    key: str
    name: str
    # SKU for products; "Parent product · Size L" for variants; "category" for categories.
    sub: str
    product_id: int | None = None
    variant_id: int | None = None
    units: int
    revenue: Decimal
    # Materials plus packaging: the same "cost of goods" the Orders page shows.
    cogs: Decimal
    failed_build_cost: Decimal
    net_profit: Decimal
    net_profit_before_failed_builds: Decimal
    margin_percent: Decimal | None
    margin_before_failed_builds_percent: Decimal | None
    profit_per_unit: Decimal | None
    share_of_profit_percent: Decimal | None
    # Failed builds as a share of all builds (failed + good), all-time. None when never built.
    fail_rate_percent: Decimal | None
    channel_units: dict[str, int]
    # Net profit per trend bucket across the requested range, for the row's sparkline.
    trend: list[Decimal]


class InsightsProducts(BaseModel):
    view: str
    rows: list[InsightsProductRow]
    total_rows: int
    page: int
    page_size: int
    # Units on lines nobody has mapped to a product yet. Money is unknown, so only units.
    unmapped_units: int
    # Units sold on orders whose financials haven't synced: counted, but money is excluded.
    profit_unavailable_units: int
    totals: InsightsTotals


class InsightsCostComponent(BaseModel):
    key: str
    label: str
    amount: Decimal


class InsightsChannelCosts(BaseModel):
    channel: str
    revenue: Decimal
    fees: Decimal
    postage: Decimal
    materials: Decimal
    packaging: Decimal
    resends: Decimal
    failed_builds: Decimal
    net_profit: Decimal


class InsightsAmountRow(BaseModel):
    key: str
    label: str
    amount: Decimal


class InsightsCosts(BaseModel):
    include_failed_builds: bool
    # Gross takings down to net profit, in order.
    waterfall: list[InsightsCostComponent]
    channels: list[InsightsChannelCosts]
    materials_by_category: list[InsightsAmountRow]
    resends_by_reason: list[InsightsAmountRow]
    # Products ranked by the material wasted on failed builds.
    failed_builds: list[InsightsProductRow]
    failed_builds_total: Decimal
    # Share of all material spend that went on failed builds, 0 to 100.
    failed_builds_percent_of_materials: Decimal | None
