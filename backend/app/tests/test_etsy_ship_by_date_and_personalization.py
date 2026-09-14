"""Two related Etsy parsing bugs, both discovered on the same real order:

1. ship_by_date was read off `receipt.expected_ship_date`, a field that does not exist
   anywhere on Etsy's ShopReceipt schema — it only exists per-transaction, on
   ShopReceiptTransaction. That made ship_by_date silently None for every Etsy order,
   ever since it was introduced.

2. variation_text (documented, and shown in the UI, as buyer personalization) kept every
   entry in transaction.variations, including real product options like Colour/Size that
   Etsy returns in the same array. Those options are already reflected in the SKU, so
   showing them again mislabeled a variation as if the buyer had typed it in.

Etsy's schema distinguishes the two kinds of variations.variations entries by
`question_id`, documented as present "[Personalization only]" — a real product option
carries property_id/value_id instead and has no question_id.
"""

from types import SimpleNamespace

from app.services.platforms.etsy import EtsyAdapter

_CONNECTION = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)


def _adapter() -> EtsyAdapter:
    """Financial enrichment (getShopPaymentByReceiptId) is a separate call this module
    isn't testing — stub it out so _parse_receipt doesn't need a real session/connection."""
    adapter = EtsyAdapter("id", "secret")

    async def _no_enrichment(*args, **kwargs):
        return SimpleNamespace(status_code=404, text="", json=lambda: {})

    adapter._authed_request = _no_enrichment
    return adapter


def test_format_variations_drops_real_product_options():
    """A Colour variation with no question_id is a SKU-determining option, not
    personalization — it must not show up as if the buyer typed it."""
    variations = [
        {"formatted_name": "Colour", "formatted_value": "Blue", "property_id": 200, "value_id": 1},
    ]
    assert EtsyAdapter._format_variations(variations) is None


def test_format_variations_keeps_true_personalization():
    variations = [
        {"formatted_name": "Colour", "formatted_value": "Blue", "property_id": 200, "value_id": 1},
        {"formatted_name": "Personalization", "formatted_value": "Happy Birthday Sam", "question_id": 555},
    ]
    assert EtsyAdapter._format_variations(variations) == "Personalization: Happy Birthday Sam"


async def test_ship_by_date_comes_from_transactions_not_the_receipt():
    """Etsy's ShopReceipt has no expected_ship_date field at all — only
    ShopReceiptTransaction does. A receipt-level value (even if somehow present) must be
    ignored, and the per-transaction values used instead."""
    receipt = {
        "receipt_id": 4174522132,
        "status": "paid",
        "is_paid": True,
        "is_shipped": False,
        "name": "A Buyer",
        "create_timestamp": 1_755_000_000,
        "update_timestamp": 1_755_000_500,
        "expected_ship_date": 9_999_999_999,  # must be ignored: not a real Etsy field
        "grandtotal": {"amount": 1250, "divisor": 100, "currency_code": "GBP"},
        "transactions": [
            {
                "transaction_id": 1,
                "sku": "SKU-BLUE",
                "quantity": 1,
                "price": {"amount": 500, "divisor": 100},
                "expected_ship_date": 1_755_600_000,
                "variations": [{"formatted_name": "Colour", "formatted_value": "Blue", "property_id": 200}],
            },
            {
                "transaction_id": 2,
                "sku": "SKU-RED",
                "quantity": 1,
                "price": {"amount": 500, "divisor": 100},
                "expected_ship_date": 1_755_500_000,
            },
        ],
    }

    parsed = await _adapter()._parse_receipt(None, _CONNECTION, receipt)

    assert parsed.ship_by_date is not None
    assert int(parsed.ship_by_date.timestamp()) == 1_755_500_000  # earliest of the two lines
    assert parsed.lines[0].variation_text is None


async def test_ship_by_date_is_none_when_no_transaction_reports_one():
    receipt = {
        "receipt_id": 4174522133,
        "status": "paid",
        "is_paid": True,
        "is_shipped": False,
        "name": "A Buyer",
        "create_timestamp": 1_755_000_000,
        "update_timestamp": 1_755_000_500,
        "grandtotal": {"amount": 500, "divisor": 100, "currency_code": "GBP"},
        "transactions": [
            {"transaction_id": 1, "sku": "SKU-BLUE", "quantity": 1, "price": {"amount": 500, "divisor": 100}}
        ],
    }

    parsed = await _adapter()._parse_receipt(None, _CONNECTION, receipt)

    assert parsed.ship_by_date is None
