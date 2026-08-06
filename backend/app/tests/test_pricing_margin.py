"""Product/variant margin counts packaging, so it agrees with order net profit about
whether packaging is a cost. Mirrored client-side in PricingSection.tsx::computeMargin.
"""

from decimal import Decimal

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
