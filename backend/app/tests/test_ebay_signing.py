"""Unit tests for eBay's Digital Signatures requirement (services/platforms/ebay_signing.py)
and for the fee parsing that sits behind it.

These exist because the failure they guard was silent: eBay 403s an unsigned Sell
Finances call, _fetch_transactions swallowed it, and every eBay order imported with no
fee breakdown while the order page read "Not yet settled" — which is also exactly what a
genuinely unsettled order looks like. Nothing in the app could tell the two apart.

The signature base is the part with no forgiving failure mode: get a component order, a
quoting rule or the trailing newline wrong and eBay answers 215120 with nothing in it
that says which. So the base is asserted literally and the signature is checked against
it with the public half of the key that signed it — verifying the bytes eBay's own
verifier would, rather than round-tripping through a reimplementation that would share
this module's assumptions.
"""

import base64

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms import ebay as ebay_module
from app.services.platforms.ebay import EbayAdapter, _needs_signature
from app.services.platforms.ebay_signing import (
    EbaySigningKey,
    EbaySigningKeyMissing,
    build_signature_headers,
    content_digest,
)

_JWE = "eyJhbGciOiJBMjU2R0NNS1ciLCJlbmMiOiJBMjU2R0NNIn0.TEST.JWE.VALUE"
_FINANCES_URL = "https://apiz.ebay.com/sell/finances/v1/transaction"


def _keypair() -> tuple[EbaySigningKey, Ed25519PrivateKey]:
    """A locally generated keypair in the same encoding eBay hands back: bare base64
    PKCS#8 DER, no PEM armour."""
    private = Ed25519PrivateKey.generate()
    der = private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    return EbaySigningKey(jwe=_JWE, private_key=base64.b64encode(der).decode("ascii")), private


def _raw_signature(headers: dict[str, str]) -> bytes:
    """Unwraps `sig1=:<base64>:` — the colons are the byte-sequence literal, not padding."""
    value = headers["Signature"]
    assert value.startswith("sig1=:") and value.endswith(":")
    return base64.b64decode(value[len("sig1=:") : -1])


def test_get_signature_base_is_exact():
    key, private = _keypair()

    headers = build_signature_headers(
        key, "GET", f"{_FINANCES_URL}?filter=orderId:{{26-14962-77224}}", created=1754480000
    )

    params = '("x-ebay-signature-key" "@method" "@path" "@authority");created=1754480000'
    assert headers["Signature-Input"] == f"sig1={params}"
    assert headers["x-ebay-signature-key"] == _JWE
    # No body, so no digest — the header must be absent, not present and empty.
    assert "Content-Digest" not in headers

    expected_base = "\n".join(
        [
            f'"x-ebay-signature-key": {_JWE}',
            '"@method": GET',
            # @path only. Covering the query string as well is a well-travelled way to
            # make every filtered call fail while unfiltered ones pass.
            '"@path": /sell/finances/v1/transaction',
            '"@authority": apiz.ebay.com',
            f'"@signature-params": {params}',
        ]
    )
    private.public_key().verify(_raw_signature(headers), expected_base.encode("utf-8"))


def test_body_puts_content_digest_first_in_the_covered_components():
    key, private = _keypair()
    body = b'{"signingKeyCipher":"ED25519"}'

    headers = build_signature_headers(
        key, "POST", "https://apiz.ebay.com/sell/finances/v1/payout", body=body, created=1754480000
    )

    assert headers["Content-Digest"] == content_digest(body)
    params = '("content-digest" "x-ebay-signature-key" "@method" "@path" "@authority");created=1754480000'
    assert headers["Signature-Input"] == f"sig1={params}"

    expected_base = "\n".join(
        [
            f'"content-digest": {headers["Content-Digest"]}',
            f'"x-ebay-signature-key": {_JWE}',
            '"@method": POST',
            '"@path": /sell/finances/v1/payout',
            '"@authority": apiz.ebay.com',
            f'"@signature-params": {params}',
        ]
    )
    private.public_key().verify(_raw_signature(headers), expected_base.encode("utf-8"))


def test_content_digest_is_rfc9530_sha256():
    assert content_digest(b"") == "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"


def test_pem_armoured_private_key_is_accepted():
    """eBay documents bare base64, but armoured values circulate — an operator pasting a
    key by hand is likely to bring the header lines with it. Signing must produce the same
    bytes either way, so this asserts against the same base as the bare-base64 case."""
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")

    headers = build_signature_headers(
        EbaySigningKey(jwe=_JWE, private_key=pem), "GET", _FINANCES_URL, created=1754480000
    )

    params = '("x-ebay-signature-key" "@method" "@path" "@authority");created=1754480000'
    expected_base = "\n".join(
        [
            f'"x-ebay-signature-key": {_JWE}',
            '"@method": GET',
            '"@path": /sell/finances/v1/transaction',
            '"@authority": apiz.ebay.com',
            f'"@signature-params": {params}',
        ]
    )
    private.public_key().verify(_raw_signature(headers), expected_base.encode("utf-8"))


@pytest.mark.parametrize(
    "url, expected",
    [
        (_FINANCES_URL, True),
        # Matched on path, not host — the Finances API is served from apiz.ebay.com while
        # everything else uses api.ebay.com, and signing must not depend on which.
        ("https://api.ebay.com/sell/finances/v1/transaction", True),
        ("https://apiz.ebay.com/sell/finances/v1/payout?limit=5", True),
        # Unsigned on purpose — see _SIGNED_PATH_PREFIXES. eBay is not enforcing
        # signatures on these for this seller, and signing them would put working order
        # sync behind an unverified signature.
        ("https://api.ebay.com/sell/fulfillment/v1/order", False),
        ("https://api.ebay.com/sell/inventory/v1/bulk_update_price_quantity", False),
    ],
)
def test_signed_paths(url, expected):
    assert _needs_signature(url) is expected


class _RecordingClient:
    """httpx.AsyncClient stand-in that records the fully-built request, so what actually
    goes on the wire is asserted against rather than what we meant to send."""

    sent: list[httpx.Request] = []
    response = httpx.Response(200, json={"transactions": []})

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, **kwargs):
        return httpx.Request(method, httpx.URL(url, params=kwargs.get("params")), headers=kwargs.get("headers"))

    async def send(self, request):
        _RecordingClient.sent.append(request)
        return _RecordingClient.response

    async def request(self, method, url, **kwargs):
        return await self.send(self.build_request(method, url, **kwargs))


class _Connection:
    """Just enough of PlatformConnection for the transport — _ensure_fresh is stubbed out
    in every test that uses this, so the token fields are never actually consulted."""

    access_token = "token"
    refresh_token = "refresh"
    access_token_expires_at = None
    last_orders_synced_at = None


async def _noop(*args, **kwargs):
    return None


@pytest.fixture
def recording(monkeypatch):
    _RecordingClient.sent = []
    _RecordingClient.response = httpx.Response(200, json={"transactions": []})
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient


def _adapter(monkeypatch, signing_key=None) -> EbayAdapter:
    adapter = EbayAdapter("id", "secret", PlatformEnvironment.production, signing_key=signing_key)
    monkeypatch.setattr(adapter, "_ensure_fresh", _noop)
    return adapter


async def test_finances_call_carries_the_signature_headers(recording, monkeypatch):
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    request = recording.sent[-1]
    assert request.headers["x-ebay-signature-key"] == _JWE
    assert request.headers["Signature-Input"].startswith("sig1=(")
    assert request.headers["Signature"].startswith("sig1=:")


async def test_missing_signing_key_degrades_instead_of_failing_the_sync(recording, monkeypatch, caplog):
    """A missing key must cost the fee breakdown and nothing else. Letting
    EbaySigningKeyMissing escape would abort the entire order sync over a number that is
    cosmetic as far as inventory is concerned."""
    adapter = _adapter(monkeypatch, signing_key=None)

    result = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert result == (None, None, None)
    assert "Settings > Integrations" in caplog.text


def test_signing_key_missing_raises_from_the_transport_itself():
    """That degradation belongs to _fetch_transactions, not to the transport — any future
    signed call gets a loud, actionable error unless it opts into degrading."""
    adapter = EbayAdapter("id", "secret", PlatformEnvironment.production, signing_key=None)

    with pytest.raises(EbaySigningKeyMissing):
        adapter._signature_headers(httpx.Request("GET", _FINANCES_URL))


# --- fee parsing -------------------------------------------------------------------


def _sale(**overrides) -> dict:
    """eBay's real SALE transaction for order 26-14962-77224, trimmed of buyer identity.

    Captured live rather than invented, because two of its properties are the whole point
    and neither is guessable from the field names:

      * `amount` (15.14) is ALREADY net of fees — the credit booked to the seller, not the
        £18.59 the buyer paid. 18.59 - 3.45 = 15.14.
      * `totalFeeBasisAmount` (18.59) is the gross, i.e. the basis fees were computed
        against. It is a single Amount object, not a list. The old code summed it as
        though it were the fee breakdown.

    eBay's own order page for this order reads: transaction fees -£3.45, postage label
    -£3.65, order earnings £11.49 — the label being the separate SHIPPING_LABEL
    transaction covered by test_non_sale_transactions_are_ignored.
    """
    sale = {
        "transactionId": "26-14962-77224",
        "orderId": "26-14962-77224",
        "transactionType": "SALE",
        "transactionStatus": "FUNDS_AVAILABLE_FOR_PAYOUT",
        "bookingEntry": "CREDIT",
        "amount": {"value": "15.14", "currency": "GBP"},
        "totalFeeBasisAmount": {"value": "18.59", "currency": "GBP"},
        "totalFeeAmount": {"value": "3.45", "currency": "GBP"},
        "orderLineItems": [
            {
                "lineItemId": "10082666783726",
                "feeBasisAmount": {"value": "38.62", "currency": "GBP"},
                "marketplaceFees": [
                    {"feeType": "FINAL_VALUE_FEE", "amount": {"value": "2.65", "currency": "GBP"}},
                    {"feeType": "FINAL_VALUE_FEE_FIXED_PER_ORDER", "amount": {"value": "0.48", "currency": "GBP"}},
                    {"feeType": "INTERNATIONAL_FEE", "amount": {"value": "0.24", "currency": "GBP"}},
                    {"feeType": "REGULATORY_OPERATING_FEE", "amount": {"value": "0.08", "currency": "GBP"}},
                ],
            }
        ],
    }
    sale.update(overrides)
    return sale


async def test_fees_come_from_total_fee_amount(recording, monkeypatch):
    recording.response = httpx.Response(200, json={"transactions": [_sale()]})
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    fees, net, status = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert fees == "3.45"
    # Taken from `amount` as-is. Subtracting the fee from it again would give 11.69 —
    # understating the credit by the fee on every order.
    assert net == "15.14"
    assert status == "FUNDS_AVAILABLE_FOR_PAYOUT"


async def test_a_regression_to_the_fee_basis_would_be_caught(recording, monkeypatch):
    """Guards the specific wrong field. totalFeeBasisAmount is present and is 18.59, so
    any reading that goes back to it produces a fee larger than the order itself."""
    recording.response = httpx.Response(200, json={"transactions": [_sale()]})
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    fees, _net, _status = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert fees != "18.59"


async def test_fees_fall_back_to_the_per_line_breakdown(recording, monkeypatch):
    """A transaction with no rolled-up total still has the per-fee-type breakdown:
    2.65 + 0.48 + 0.24 + 0.08 = 3.45, the same number by a different route."""
    sale = _sale()
    del sale["totalFeeAmount"]
    recording.response = httpx.Response(200, json={"transactions": [sale]})
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    fees, net, _status = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert fees == "3.45"
    assert net == "15.14"


async def test_non_sale_transactions_are_ignored(recording, monkeypatch):
    """The postage label is its own transaction against the same order, and it comes
    first in eBay's real response. Reading transactions[0] would report £3.65 of postage
    as the platform fee — StockSmith already tracks postage separately, from the shipping
    profile."""
    recording.response = httpx.Response(
        200,
        json={
            "transactions": [
                {
                    "transactionId": "05-15000-27049",
                    "orderId": "26-14962-77224",
                    "transactionType": "SHIPPING_LABEL",
                    "amount": {"value": "3.65", "currency": "GBP"},
                    "bookingEntry": "DEBIT",
                    "transactionMemo": "Shipping label purchased",
                },
                _sale(),
            ],
            "total": 2,
        },
    )
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    fees, net, _status = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert fees == "3.45"
    assert net == "15.14"


async def test_non_200_is_logged_rather_than_swallowed(recording, monkeypatch, caplog):
    """The exact failure that hid this bug: 403 errorId 215001 on every call, reported
    nowhere."""
    recording.response = httpx.Response(
        403, json={"errors": [{"errorId": 215001, "message": "Missing x-ebay-signature-key header"}]}
    )
    key, _private = _keypair()
    adapter = _adapter(monkeypatch, key)

    result = await adapter._fetch_transactions(None, _Connection(), "26-14962-77224")

    assert result == (None, None, None)
    assert "215001" in caplog.text
