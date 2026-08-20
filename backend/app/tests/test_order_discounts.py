"""How a discount reaches `orders.subtotal`, on each platform.

Nothing covered discounts before this file — grepping the suite for the word returned
nothing — which is how eBay came to read `priceDiscountSubtotal`, a field its
PricingSummary does not have, on every order for months without anything erroring. A
quarter of this shop's eBay orders recorded more revenue than the buyer paid.

The rule these tests exist to hold: **`subtotal` is what the buyer paid for the items,
after any discount, on every platform.** The two marketplaces report it differently — Etsy
nets it before StockSmith ever sees it, eBay does not — so the adapters have to converge
and only tests can say whether they still do.

Parsers are driven directly on plain dicts. `enrich` is switched off by giving the
connection a sync watermark in the future, which is the documented gate in both adapters
and keeps these free of sessions, network and fixtures.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.etsy import EtsyAdapter

# Anything after `last_modified` turns enrichment off in both adapters, so no fee lookup
# is attempted and no session is touched.
_FUTURE = datetime.now(timezone.utc) + timedelta(days=365)
_PLACED = "2026-08-19T17:14:10.000Z"


def _connection():
    return SimpleNamespace(last_orders_synced_at=_FUTURE, external_account_id="shop-1")


def _ebay_order(pricing: dict, *, order_id: str = "10-15032-41755") -> dict:
    return {
        "orderId": order_id,
        "creationDate": _PLACED,
        "lastModifiedDate": _PLACED,
        "orderPaymentStatus": "PAID",
        "orderFulfillmentStatus": "FULFILLED",
        "buyer": {"username": "a-buyer"},
        "lineItems": [
            {
                "lineItemId": "1",
                "sku": "SKU-0026-INNER",
                "quantity": 6,
                "lineItemCost": {"value": "23.94", "currency": "GBP"},
            }
        ],
        "pricingSummary": pricing,
    }


def _money(value: str) -> dict:
    return {"value": value, "currency": "GBP"}


async def _parse_ebay(pricing: dict, **kwargs):
    return await EbayAdapter("id", "secret")._parse_order(None, _connection(), _ebay_order(pricing, **kwargs))


# --- eBay --------------------------------------------------------------------------------


async def test_ebay_price_discount_is_netted_into_subtotal():
    """The live case: six units at £3.99 with a £3.60 volume discount.

    eBay charged £24.94 and StockSmith recorded £28.54 of revenue, because priceSubtotal
    is the items *before* the discount and the discount was never read.
    """
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("23.94"),
            "priceDiscount": _money("3.60"),
            "deliveryCost": _money("4.60"),
            "tax": _money("0.00"),
            "total": _money("24.94"),
        }
    )

    assert order.subtotal == "20.34"
    assert order.discount_amount == "3.60"
    assert order.shipping_charged == "4.60"
    # And the whole thing now adds up to what eBay says the buyer paid.
    assert Decimal(order.subtotal) + Decimal(order.shipping_charged) == Decimal(order.grand_total)


async def test_ebay_additional_savings_count_as_a_discount():
    """A coupon lands in additionalSavings, a separate term in eBay's own total formula."""
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("23.94"),
            "additionalSavings": _money("2.00"),
            "deliveryCost": _money("4.60"),
            "total": _money("26.54"),
        }
    )

    assert order.discount_amount == "2.00"
    assert order.subtotal == "21.94"


async def test_ebay_sums_a_promotion_and_a_coupon_together():
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("23.94"),
            "priceDiscount": _money("3.60"),
            "additionalSavings": _money("2.00"),
            "deliveryCost": _money("4.60"),
            "total": _money("22.94"),
        }
    )

    assert order.discount_amount == "5.60"
    assert order.subtotal == "18.34"


async def test_ebay_negative_signed_discounts_are_handled():
    """eBay's docs give its total formula both ways — subtracting these terms in one
    place, calling them negative numbers in another — and deliveryDiscount is known to
    arrive negative. Reading the sign would be betting on which convention a field
    follows, and losing turns a discount into a surcharge in silence."""
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("23.94"),
            "priceDiscount": _money("-3.60"),
            "deliveryCost": _money("4.60"),
            "total": _money("24.94"),
        }
    )

    assert order.discount_amount == "3.60"
    assert order.subtotal == "20.34"


async def test_ebay_delivery_discount_stays_out_of_the_item_discount():
    """eBay documents priceDiscount as excluding delivery discounts, and _net_money
    already nets those into what the buyer paid for postage. Counting them in both places
    would take the same money off twice."""
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("23.94"),
            "deliveryCost": _money("7.20"),
            "deliveryDiscount": _money("-3.40"),
            "total": _money("27.74"),
        }
    )

    assert order.shipping_charged == "3.80"
    assert order.discount_amount is None
    assert order.subtotal == "23.94"


async def test_ebay_order_without_a_discount_is_unchanged():
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("9.99"),
            "deliveryCost": _money("3.60"),
            "total": _money("13.59"),
        }
    )

    assert order.discount_amount is None
    assert order.subtotal == "9.99"


async def test_ebay_zero_discount_reads_as_no_discount():
    """So the panel doesn't render "£9.99 − £0.00 discount" under every order."""
    order = await _parse_ebay(
        {
            "priceSubtotal": _money("9.99"),
            "priceDiscount": _money("0.00"),
            "deliveryCost": _money("3.60"),
            "total": _money("13.59"),
        }
    )

    assert order.discount_amount is None


async def test_ebay_warns_when_an_order_does_not_reconcile(caplog):
    """The check that would have caught this years ago. Nothing is derived from it — the
    figures still come from eBay's own fields — it just refuses to stay quiet."""
    with caplog.at_level("WARNING", logger="stocksmith.ebay"):
        order = await _parse_ebay(
            {
                "priceSubtotal": _money("23.94"),
                "deliveryCost": _money("4.60"),
                "total": _money("24.94"),  # £3.60 short: a discount nothing read
            },
            order_id="10-15032-41755",
        )

    assert order.subtotal == "23.94"  # still reports what eBay said, unaltered
    assert "10-15032-41755" in caplog.text
    assert "does not reconcile" in caplog.text


async def test_ebay_stays_quiet_when_an_order_reconciles(caplog):
    with caplog.at_level("WARNING", logger="stocksmith.ebay"):
        await _parse_ebay(
            {
                "priceSubtotal": _money("9.99"),
                "deliveryCost": _money("3.60"),
                "total": _money("13.59"),
            }
        )

    assert "does not reconcile" not in caplog.text


# --- Etsy --------------------------------------------------------------------------------


async def test_etsy_subtotal_is_already_net_of_its_discount():
    """Etsy's receipt.subtotal has the discount taken off before StockSmith sees it.

    Confirmed against the live database: order 4149598334 has an item price of £6.99, a
    £1.40 coupon, and a stored subtotal of £5.59 — all 19 discounted Etsy orders behave
    this way. This is the asymmetry the eBay change exists to close, so it is pinned here:
    anyone who later makes Etsy subtract its discount again will fail this test rather
    than quietly halve a shop's revenue.
    """
    receipt = {
        "receipt_id": 4149598334,
        "status": "completed",
        "is_paid": True,
        "is_shipped": True,
        "create_timestamp": int(datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp()),
        "update_timestamp": int(datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp()),
        "transactions": [],
        "grandtotal": {"amount": 919, "divisor": 100, "currency_code": "GBP"},
        "subtotal": {"amount": 559, "divisor": 100, "currency_code": "GBP"},
        "total_shipping_cost": {"amount": 360, "divisor": 100, "currency_code": "GBP"},
        "discount_amt": {"amount": 140, "divisor": 100, "currency_code": "GBP"},
    }

    order = await EtsyAdapter("id", "secret")._parse_receipt(None, _connection(), receipt)

    assert order.subtotal == "5.59"
    assert order.discount_amount == "1.40"
    # 5.59 + 3.60 = 9.19, the same reconciliation the eBay side now has to satisfy.
    assert Decimal(order.subtotal) + Decimal(order.shipping_charged) == Decimal(order.grand_total)


# --- the point of all of it --------------------------------------------------------------


@pytest.mark.parametrize("platform", ["etsy", "ebay"])
async def test_net_profit_counts_a_discount_once(session, platform):
    """The same sale, discounted the same way, on either marketplace: one net profit.

    _compute_net_profit never subtracts discount_amount — it doesn't need to, because
    subtotal is what the buyer paid. That is only true while both adapters agree, which is
    what this asserts. Items £6.99 less a £1.40 discount is £5.59, plus £3.60 postage, less
    £1.44 fees: £7.75. Counting the discount a second time would give £6.35.
    """
    from app.models.order import Order, OrderStatus
    from app.routers.orders import _compute_net_profit

    if platform == "etsy":
        parsed = await EtsyAdapter("id", "secret")._parse_receipt(
            None,
            _connection(),
            {
                "receipt_id": 1,
                "status": "completed",
                "is_paid": True,
                "create_timestamp": int(datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp()),
                "update_timestamp": int(datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp()),
                "transactions": [],
                "grandtotal": {"amount": 919, "divisor": 100, "currency_code": "GBP"},
                "subtotal": {"amount": 559, "divisor": 100, "currency_code": "GBP"},
                "total_shipping_cost": {"amount": 360, "divisor": 100, "currency_code": "GBP"},
                "discount_amt": {"amount": 140, "divisor": 100, "currency_code": "GBP"},
            },
        )
    else:
        parsed = await _parse_ebay(
            {
                "priceSubtotal": _money("6.99"),
                "priceDiscount": _money("1.40"),
                "deliveryCost": _money("3.60"),
                "total": _money("9.19"),
            }
        )

    assert parsed.subtotal == "5.59"
    assert parsed.discount_amount == "1.40"

    order = Order(
        platform=platform,
        external_order_id=f"{platform}-1",
        status=OrderStatus.shipped,
        order_placed_at=datetime.now(timezone.utc),
        subtotal=Decimal(parsed.subtotal),
        shipping_charged=Decimal(parsed.shipping_charged),
        discount_amount=Decimal(parsed.discount_amount),
        payment_fees=Decimal("1.44"),
    )
    session.add(order)
    await session.flush()

    assert _compute_net_profit(order, None, None) == Decimal("7.75")
