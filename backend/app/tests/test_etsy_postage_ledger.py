"""EtsyAdapter._extract_postage_charges — the best-effort label matcher over the
payment-account ledger. Etsy doesn't document how a label posts, so the rules here are
deliberately conservative (marker AND reference required) and everything left over that
references the receipt is logged for confirmation against a live shop."""

import logging

from app.services.platforms.etsy import EtsyAdapter

_RECEIPT = 4128199713
_TX = "9000001"


def _entry(entry_id, ledger_type, reference_type, reference_id, amount, description=None, created=1_757_000_000):
    return {
        "entry_id": entry_id,
        "ledger_type": ledger_type,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "amount": amount,
        "currency": "GBP",
        "description": description or ledger_type,
        "created_timestamp": created,
    }


def test_labels_referencing_the_receipt_are_extracted_oldest_first():
    entries = [
        _entry(3, "shipping_label", "receipt", _RECEIPT, -340, "Shipping label purchase", created=1_757_500_000),
        _entry(1, "shipping_label", "receipt", _RECEIPT, -310, "Shipping label purchase", created=1_757_000_000),
        # The FEE on the buyer's postage — already counted as a platform fee, not a label.
        _entry(2, "shipping_transaction", "receipt", _RECEIPT, -22),
        # Another receipt's label.
        _entry(4, "shipping_label", "receipt", _RECEIPT + 1, -310, "Shipping label purchase"),
        # A label-shaped credit (refund/void) is not a purchase.
        _entry(5, "shipping_label_refund", "receipt", _RECEIPT, 310, "Shipping label refund"),
        # Fee/VAT rows the fee total already handles.
        _entry(6, "transaction", "transaction", _TX, -65),
        _entry(7, "vat_seller_services", "receipt", _RECEIPT, -13, "vat_seller_services"),
    ]

    charges = EtsyAdapter._extract_postage_charges(entries, _RECEIPT, {_TX})

    assert [(c.external_id, c.amount, c.currency) for c in charges] == [("1", "3.10", "GBP"), ("3", "3.40", "GBP")]
    assert charges[0].posted_at is not None and charges[0].posted_at < charges[1].posted_at


def test_a_label_keyed_on_a_transaction_or_its_own_reference_type_still_matches():
    entries = [
        _entry(1, "shipping_label", "transaction", _TX, -310),
        _entry(2, "shipping_label_purchase", "shipping_label", "77", -420),
    ]
    charges = EtsyAdapter._extract_postage_charges(entries, _RECEIPT, {_TX})
    assert sorted(c.amount for c in charges) == ["3.10", "4.20"]


def test_unrecognised_entries_on_the_receipt_are_logged_for_confirmation(caplog):
    caplog.set_level(logging.INFO, logger="stocksmith.etsy")
    entries = [
        _entry(1, "mystery_debit", "receipt", _RECEIPT, -99, "Something new"),
        _entry(2, "mystery_debit", "receipt", _RECEIPT + 5, -99, "Someone else's"),
    ]
    assert EtsyAdapter._extract_postage_charges(entries, _RECEIPT, {_TX}) == []
    assert "mystery_debit" in caplog.text and "Something new" in caplog.text
    assert "Someone else's" not in caplog.text
