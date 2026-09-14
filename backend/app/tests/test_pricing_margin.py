"""Product/variant margin counts packaging, so it agrees with order net profit about
whether packaging is a cost, and counts postage charged as revenue, so it agrees with
order net profit about what income is. Mirrored client-side in
PricingSection.tsx::computeMargin.
"""

from decimal import Decimal

from app.models.platform_fee import FeeBasis, MarginFeeSource, PlatformFeeComponent
from app.services.platform_fees import resolve_fee_percent
from app.services.pricing import compute_profit_margin


def test_margin_includes_kitting_cost_per_unit():
    sale_price = Decimal("20")
    without = compute_profit_margin(sale_price, Decimal("5"), Decimal("3"), Decimal("10"))
    with_kitting = compute_profit_margin(sale_price, Decimal("5"), Decimal("3"), Decimal("10"), Decimal("2"))

    # 20 - 5 build - 3 shipping - 2 fee (10% of 20) = 10, then packaging comes straight off
    assert without[0] == Decimal("10")
    assert with_kitting[0] == Decimal("8")
    assert with_kitting[1] == Decimal("40")  # 8/20


def test_margin_unchanged_when_kitting_omitted():
    """Defaulted, so callers that predate the parameter keep their old numbers."""
    assert compute_profit_margin(Decimal("20"), Decimal("5"), Decimal("3"), Decimal("10")) == compute_profit_margin(
        Decimal("20"), Decimal("5"), Decimal("3"), Decimal("10"), None
    )


def test_no_sale_price_is_still_none():
    assert compute_profit_margin(None, Decimal("5"), Decimal("3"), Decimal("10"), Decimal("2")) == (None, None)


def test_postage_charged_is_revenue_and_the_fee_base():
    """A £20 item posted for £4 is a £24 sale. The fee is 10% of the £24 the buyer pays,
    postage cost still comes off, and margin is over the £24 — the same shape as
    orders._compute_net_profit (subtotal + shipping_charged - fees - postage cost - COGS)."""
    profit, margin = compute_profit_margin(
        Decimal("20"), Decimal("5"), Decimal("3"), Decimal("10"), Decimal("2"), shipping_price=Decimal("4")
    )
    # 24 - 5 build - 2 packaging - 3 postage cost - 2.40 fee
    assert profit == Decimal("11.60")
    assert margin == Decimal("11.60") / Decimal("24") * 100


def test_postage_that_exactly_recovers_its_cost_only_costs_its_fee():
    """The "buyer-recovered postage" case falls out of the arithmetic rather than needing a
    toggle: charging what it costs nets to zero except for the fee levied on it."""
    flat, _ = compute_profit_margin(Decimal("20"), Decimal("5"), None, Decimal("10"))
    recovered, _ = compute_profit_margin(Decimal("20"), Decimal("5"), Decimal("4"), Decimal("10"), shipping_price=Decimal("4"))
    assert flat - recovered == Decimal("4") * Decimal("10") / 100


def _component(basis: FeeBasis, rate_percent=None, fixed_amount=None, display_order=0) -> PlatformFeeComponent:
    return PlatformFeeComponent(
        platform="etsy",
        name="x",
        basis=basis,
        rate_percent=rate_percent,
        fixed_amount=fixed_amount,
        display_order=display_order,
        enabled=True,
    )


def test_calculated_fee_percent_is_expressed_over_revenue():
    """A 10% component on sale price + shipping must come back as 10%, not inflated by
    shipping/sale_price, or compute_profit_margin would apply it to revenue a second time."""
    components = [_component(FeeBasis.sale_price_plus_shipping, rate_percent=10)]
    assert resolve_fee_percent(MarginFeeSource.etsy, components, None, Decimal("20"), Decimal("4")) == Decimal("10")


def test_calculated_fee_on_sale_price_only_deflates_against_revenue():
    """A component levied on the item price alone is a smaller share of what the buyer
    pays once postage is in the base. The £ amount is unchanged either way."""
    components = [_component(FeeBasis.sale_price, rate_percent=12)]
    pct = resolve_fee_percent(MarginFeeSource.etsy, components, None, Decimal("20"), Decimal("4"))
    assert pct == Decimal("2.40") / Decimal("24") * 100
    profit, _ = compute_profit_margin(Decimal("20"), None, None, pct, shipping_price=Decimal("4"))
    assert profit == Decimal("24") - Decimal("2.40")


def test_manual_fee_percent_passes_through_untouched():
    assert resolve_fee_percent(MarginFeeSource.manual, [], Decimal("13"), Decimal("20"), Decimal("4")) == Decimal("13")


def test_zero_priced_free_postage_has_no_fee_percent():
    components = [_component(FeeBasis.sale_price_plus_shipping, fixed_amount=Decimal("0.30"))]
    assert resolve_fee_percent(MarginFeeSource.etsy, components, None, Decimal("0"), Decimal("0")) is None
