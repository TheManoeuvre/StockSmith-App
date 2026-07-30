"""Truth tables for the two adapter payment-state parsers.

These are pure functions on plain dicts precisely so this file needs no session, no
network and no fixtures — the parsing is where the original bug lived, so it gets the
most direct coverage available.
"""

from types import SimpleNamespace

import pytest

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms.base import PaymentState
from app.services.platforms.ebay import EbayAdapter, _order_payment_state
from app.services.platforms.etsy import _receipt_payment_state

# Every value of Etsy's documented receipt status enum.
_ETSY_STATUSES = [
    "paid",
    "completed",
    "open",
    "payment processing",
    "canceled",
    "fully refunded",
    "partially refunded",
]


@pytest.mark.parametrize("status", _ETSY_STATUSES)
def test_etsy_is_paid_false_always_unsettled_except_refund(status):
    """An explicit is_paid=False beats any status — that single fact is the fix.

    The one exception is a fully-refunded receipt, which is a distinct outcome (money
    arrived and then left) rather than money never having arrived.
    """
    state = _receipt_payment_state({"is_paid": False, "status": status})
    expected = PaymentState.reversed if status == "fully refunded" else PaymentState.unsettled
    assert state is expected


@pytest.mark.parametrize("status", _ETSY_STATUSES)
def test_etsy_is_paid_true_is_settled_except_refund(status):
    state = _receipt_payment_state({"is_paid": True, "status": status})
    expected = PaymentState.reversed if status == "fully refunded" else PaymentState.settled
    assert state is expected


@pytest.mark.parametrize(
    "status,expected",
    [
        ("paid", PaymentState.settled),
        ("completed", PaymentState.settled),
        ("partially refunded", PaymentState.settled),
        ("open", PaymentState.unsettled),
        ("payment processing", PaymentState.unsettled),
        ("canceled", PaymentState.unsettled),
        ("fully refunded", PaymentState.reversed),
    ],
)
def test_etsy_falls_back_to_status_when_is_paid_absent(status, expected):
    assert _receipt_payment_state({"status": status}) is expected


def test_etsy_empty_receipt_fails_closed():
    """No usable signal at all must never read as paid."""
    assert _receipt_payment_state({}) is PaymentState.unsettled


def test_etsy_status_casing_is_ignored():
    assert _receipt_payment_state({"status": "Fully Refunded"}) is PaymentState.reversed
    assert _receipt_payment_state({"status": "PAID"}) is PaymentState.settled


# --- The live regression this whole change exists for -----------------------------
#
# Three payment-endpoint status strings were observed on one real shop. Two mean paid and
# one does not, which is why that field must never gate anything. These cases pin the
# behaviour using the receipts as they actually appeared.


def test_klarna_instalment_in_progress_is_not_imported():
    """Receipt 4128199713: INSTALL_IN_PROGRESS, absent from the seller's Etsy UI entirely.

    Imported before the gate existed, where it sat as a normal pending order.
    """
    receipt = {"receipt_id": 4128199713, "is_paid": False, "status": "payment processing"}
    assert _receipt_payment_state(receipt) is PaymentState.unsettled


def test_posted_payment_status_is_still_a_real_paid_order():
    """Receipt 4128127298: payment status POSTED, and visible in the Etsy UI awaiting
    shipment — a genuine paid order.

    A gate built on the payment status string would have wrongly excluded (and, during
    cleanup, deleted) this one. The receipt's own is_paid says paid, and that is what the
    parser reads.
    """
    receipt = {"receipt_id": 4128127298, "is_paid": True, "status": "paid"}
    assert _receipt_payment_state(receipt) is PaymentState.settled


def test_payment_endpoint_status_string_is_never_consulted():
    """Whatever undocumented value the Payments endpoint reports, it must not move the
    needle — only the receipt's own fields decide."""
    for observed in ("SETTLED", "POSTED", "INSTALL_IN_PROGRESS", "SOMETHING_NEW"):
        assert _receipt_payment_state({"is_paid": True, "status": "paid", "payment_status": observed}) is (
            PaymentState.settled
        )
        assert _receipt_payment_state({"is_paid": False, "status": "open", "payment_status": observed}) is (
            PaymentState.unsettled
        )


# --- eBay -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("PAID", PaymentState.settled),
        ("PARTIALLY_REFUNDED", PaymentState.settled),
        ("FULLY_REFUNDED", PaymentState.reversed),
        ("PENDING", PaymentState.unsettled),
        ("FAILED", PaymentState.unsettled),
    ],
)
def test_ebay_covers_every_documented_enum_value(status, expected):
    assert _order_payment_state({"orderPaymentStatus": status}) is expected


@pytest.mark.parametrize("order", [{}, {"orderPaymentStatus": None}, {"orderPaymentStatus": "SOME_NEW_STATE"}])
def test_ebay_unknown_or_missing_fails_closed(order):
    """A future enum addition must not silently import as paid."""
    assert _order_payment_state(order) is PaymentState.unsettled


def test_ebay_lowercase_is_accepted():
    assert _order_payment_state({"orderPaymentStatus": "paid"}) is PaymentState.settled


async def test_ebay_failed_payment_is_not_a_cancellation():
    """`_parse_order` used to fold orderPaymentStatus == FAILED into is_cancelled,
    conflating "eBay was never paid" with "the order was called off".

    Reachable without any network because FAILED maps to unsettled, which suppresses the
    Finances enrichment call — the same property that makes an unpaid order free to sync.
    """
    adapter = EbayAdapter("id", "secret", PlatformEnvironment.production)
    connection = SimpleNamespace(last_orders_synced_at=None)
    raw = {
        "orderId": "12-3456-7890",
        "orderPaymentStatus": "FAILED",
        "creationDate": "2026-07-27T15:40:37.000Z",
        "lastModifiedDate": "2026-07-27T15:40:37.000Z",
        "lineItems": [],
        "pricingSummary": {},
    }

    parsed = await adapter._parse_order(None, connection, raw)

    assert parsed.is_cancelled is False
    assert parsed.payment_state is PaymentState.unsettled
    assert parsed.financials_enriched is False


async def test_ebay_cancel_status_still_marks_cancelled():
    adapter = EbayAdapter("id", "secret", PlatformEnvironment.production)
    connection = SimpleNamespace(last_orders_synced_at=None)
    raw = {
        "orderId": "12-3456-7891",
        "orderPaymentStatus": "PENDING",
        "cancelStatus": {"cancelState": "CANCELED"},
        "creationDate": "2026-07-27T15:40:37.000Z",
        "lineItems": [],
        "pricingSummary": {},
    }

    parsed = await adapter._parse_order(None, connection, raw)

    assert parsed.is_cancelled is True
