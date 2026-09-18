"""An Etsy listing whose quantity isn't attached to a variation can never accept a per-SKU
quantity write: every attempt comes back
`400 {"error":"quantity must be consistent across all products"}`.

Treating that as an ordinary push error means the reconcile sweep re-queues it on every
hourly tick, forever, spending daily API budget on a call that cannot succeed and lighting
a badge no retry will ever clear. These tests pin the detection and the classification —
PlatformPushBlockedError, not PlatformSyncError — that keeps the two apart.
"""

import pytest

from app.services.platforms.base import ExternalListingRef
from app.services.platforms.errors import PlatformPushBlockedError, PlatformSyncError
from app.services.platforms.etsy import EtsyAdapter


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _product(sku: str, quantity: int, **kw) -> dict:
    return {
        "sku": sku,
        "property_values": [],
        "offerings": [
            {"quantity": quantity, "is_enabled": True, "readiness_state_id": 1, "price": {"amount": 500, "divisor": 100}}
        ],
        **kw,
    }


def _inventory(products: list[dict], quantity_on_property: list[int]) -> dict:
    return {
        "products": products,
        "price_on_property": [],
        "quantity_on_property": quantity_on_property,
        "sku_on_property": [],
    }


class _FakeEtsy(EtsyAdapter):
    def __init__(self, inventory: dict, put_response: _FakeResponse | None = None):
        self._inventory = inventory
        self._put_response = put_response or _FakeResponse(200, {})
        self.calls: list[tuple[str, str]] = []

    async def _authed_request(self, session, connection, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "GET":
            return _FakeResponse(200, self._inventory)
        return self._put_response


_REF = ExternalListingRef(
    external_listing_id="L1", title="t", sku="SKU-A", state="active", quantity=0, variation=None
)


async def test_multi_variation_listing_without_quantity_on_property_is_blocked():
    adapter = _FakeEtsy(_inventory([_product("SKU-A", 5), _product("SKU-B", 5)], quantity_on_property=[]))

    with pytest.raises(PlatformPushBlockedError) as excinfo:
        await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert adapter.calls == [("GET", "/listings/L1/inventory")], "no PUT — the 400 is a foregone conclusion"
    # The message is shown to the seller verbatim, so it has to name the fix, not the symptom.
    assert "variation" in str(excinfo.value)


async def test_blocked_is_not_a_sync_error():
    """The distinction the reconcile sweep reads. If PlatformPushBlockedError ever became a
    PlatformSyncError subclass, the hourly retry would silently come back."""
    adapter = _FakeEtsy(_inventory([_product("SKU-A", 5), _product("SKU-B", 5)], quantity_on_property=[]))

    with pytest.raises(PlatformPushBlockedError) as excinfo:
        await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert not isinstance(excinfo.value, PlatformSyncError)


async def test_multi_variation_listing_with_quantity_on_property_pushes_normally():
    """The healthy shape of the same listing: quantity varies by variation, so a per-SKU
    write is exactly what Etsy expects."""
    adapter = _FakeEtsy(_inventory([_product("SKU-A", 5), _product("SKU-B", 5)], quantity_on_property=[513]))

    await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


async def test_single_product_listing_is_not_blocked():
    """A listing with one product has nothing to be inconsistent with — an empty
    quantity_on_property is the ordinary shape there, not a fault."""
    adapter = _FakeEtsy(_inventory([_product("SKU-A", 5)], quantity_on_property=[]))

    await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


async def test_deleted_products_do_not_make_a_listing_look_multi_variation():
    adapter = _FakeEtsy(
        _inventory([_product("SKU-A", 5), _product("SKU-OLD", 5, is_deleted=True)], quantity_on_property=[])
    )

    await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


async def test_a_listing_already_at_the_target_quantity_is_not_marked_blocked():
    """No write is needed, so nothing is blocked today. Marking it would report a problem
    the seller has no reason to act on — and would stop the sweep re-asserting a listing
    that is, as far as anyone can tell, fine."""
    adapter = _FakeEtsy(_inventory([_product("SKU-A", 9), _product("SKU-B", 5)], quantity_on_property=[]))

    await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)

    assert adapter.calls == [("GET", "/listings/L1/inventory")]


async def test_the_400_body_is_classified_as_blocked_if_the_preflight_check_misses_it():
    """Backstop for a configuration shape the GET-side check doesn't recognise. Etsy's own
    verdict is the final word on whether a write can ever land."""
    adapter = _FakeEtsy(
        _inventory([_product("SKU-A", 5)], quantity_on_property=[]),
        put_response=_FakeResponse(400, {}, text='{"error":"quantity must be consistent across all products"}'),
    )

    with pytest.raises(PlatformPushBlockedError):
        await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)


async def test_an_ordinary_400_is_still_a_sync_error():
    adapter = _FakeEtsy(
        _inventory([_product("SKU-A", 5)], quantity_on_property=[]),
        put_response=_FakeResponse(400, {}, text='{"error":"Array contains invalid keys"}'),
    )

    with pytest.raises(PlatformSyncError):
        await adapter.push_listing_quantity(None, None, _REF, "SKU-A", 9)
