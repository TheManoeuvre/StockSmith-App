"""The Etsy sync must survive a failed financial-enrichment call.

This is the regression that stopped a real shop syncing for two days. Enrichment
(getShopPaymentByReceiptId + the payment-account ledger crawl) is best-effort by design —
a non-200 response has always just left the breakdown empty. A transport-level failure
was the hole: it raised straight out of _parse_receipt, past fetch_orders_since, and
aborted the whole run.

That failure mode is self-perpetuating, which is why it needs its own coverage rather
than being treated as bad luck. Aborting means the watermark never advances, so the next
run re-fetches the same receipts and enriches every one of them again — and a freshly
reconnected shop (disconnect clears last_orders_synced_at) has that gate open for every
receipt back to sync_start_date, hundreds of sequential calls where one timeout is close
to certain.
"""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode
from app.services import order_sync
from app.services.platforms.base import PaymentState
from app.services.platforms.etsy import EtsyAdapter

# A settled, shipped receipt: settled opens the enrichment gate, shipped additionally
# opens the ledger crawl — the exact call that timed out in the live failure.
_RECEIPT = {
    "receipt_id": 3141592,
    "status": "paid",
    "is_paid": True,
    "is_shipped": True,
    "name": "A Buyer",
    "create_timestamp": 1_755_000_000,
    "update_timestamp": 1_755_000_500,
    "grandtotal": {"amount": 1250, "divisor": 100, "currency_code": "GBP"},
    "transactions": [
        {"transaction_id": 27, "sku": "SKU-0001", "quantity": 2, "price": {"amount": 500, "divisor": 100}}
    ],
}


def _adapter_raising(exc: Exception) -> EtsyAdapter:
    adapter = EtsyAdapter("id", "secret")

    async def _boom(*args, **kwargs):
        raise exc

    adapter._authed_request = _boom
    return adapter


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout(""),
        httpx.ReadTimeout(""),
        httpx.ConnectError("[Errno 11001] getaddrinfo failed"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_transport_failure_degrades_instead_of_aborting(exc):
    """The order still parses; only the money breakdown is missing."""
    # last_orders_synced_at=None is the freshly-reconnected state that makes every
    # receipt eligible for enrichment.
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)

    parsed = await _adapter_raising(exc)._parse_receipt(None, connection, _RECEIPT)

    assert parsed.external_order_id == "3141592"
    assert parsed.payment_state is PaymentState.settled
    assert parsed.is_shipped is True
    # The order and its lines are intact — this is what has to keep importing.
    assert [(line.sku, line.qty) for line in parsed.lines] == [("SKU-0001", 2)]
    assert parsed.grand_total == "12.50"
    # ...and the breakdown is absent rather than wrong, flagged so _apply_financials
    # leaves any previously-stored breakdown alone.
    assert parsed.financials_enriched is False
    assert parsed.payment_fees is None
    assert parsed.payment_net is None


async def test_rate_limit_still_aborts_the_run():
    """Deliberately not swallowed: a quota is not a one-off, so every remaining receipt
    would fail too, and spending the rest of the daily budget to collect a batch of blank
    breakdowns is worse than stopping."""
    from app.services.platforms.errors import PlatformRateLimitError

    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)
    adapter = _adapter_raising(PlatformRateLimitError("Etsy API rate limit exceeded"))

    with pytest.raises(PlatformRateLimitError):
        await adapter._parse_receipt(None, connection, _RECEIPT)


async def test_unsettled_receipt_never_reaches_enrichment():
    """The gate that makes an unpaid receipt cost zero API calls also makes it immune to
    this failure — pinned so the two stay linked."""
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)
    adapter = _adapter_raising(AssertionError("enrichment must not be attempted"))

    parsed = await adapter._parse_receipt(None, connection, {**_RECEIPT, "is_paid": False, "status": "open"})

    assert parsed.payment_state is PaymentState.unsettled
    assert parsed.financials_enriched is False


# --- The sync log has to be able to say what went wrong ---------------------------


async def test_timeout_is_logged_with_a_readable_message(session):
    """httpx's timeouts stringify to "", which the run log used to store verbatim: the
    sync panel showed "failed" with nothing beside it, which is exactly why the live
    failure went two days without a diagnosis."""
    await order_sync._record_failure(session, ListingPlatform.etsy, SyncRunMode.commit, httpx.ConnectTimeout(""))

    run = (await session.execute(select(PlatformSyncRun))).scalars().one()
    assert run.error_message
    assert "ConnectTimeout" in run.error_message


def test_errors_that_already_read_well_are_left_alone():
    """Messages written for the user must not get a class name bolted onto the front."""
    from app.services.platforms.errors import PlatformAuthError

    assert order_sync._describe_error(PlatformAuthError("Etsy connection has no refresh token — reconnect required")) == (
        "Etsy connection has no refresh token — reconnect required"
    )
