"""Stage 3c: push_listing_quantity must not spend a write (and a slice of the daily API
budget) re-sending a quantity Etsy already holds. updateListingInventory replaces the
listing's entire inventory record, so a no-op PUT is far from free.
"""

import httpx
import pytest

from app.services.platforms.etsy import EtsyAdapter
from app.services.platforms.base import ExternalListingRef


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def _inventory(sku: str, quantity: int, is_enabled: bool = True) -> dict:
    return {
        "products": [
            {
                "sku": sku,
                "property_values": [],
                "offerings": [
                    {"quantity": quantity, "is_enabled": is_enabled, "readiness_state_id": 1, "price": {"amount": 500, "divisor": 100}}
                ],
            }
        ],
        "price_on_property": [],
        "quantity_on_property": [],
        "sku_on_property": [],
    }


class _RecordingEtsy(EtsyAdapter):
    def __init__(self, current_qty: int):
        self._current_qty = current_qty
        self.calls: list[tuple[str, str]] = []

    async def _authed_request(self, session, connection, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "GET":
            return _FakeResponse(200, _inventory("SKU-1", self._current_qty))
        return _FakeResponse(200, {})


_REF = ExternalListingRef(
    external_listing_id="L1", title="t", sku="SKU-1", state="active", quantity=0, variation=None
)


async def test_skips_put_when_quantity_already_matches():
    adapter = _RecordingEtsy(current_qty=7)

    await adapter.push_listing_quantity(None, None, _REF, "SKU-1", 7)

    assert adapter.calls == [("GET", "/listings/L1/inventory")], "GET only, no PUT"


async def test_writes_when_quantity_differs():
    adapter = _RecordingEtsy(current_qty=7)

    await adapter.push_listing_quantity(None, None, _REF, "SKU-1", 4)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


async def test_writes_when_only_the_enabled_flag_differs():
    # Etsy holds quantity 1 but disabled (its out-of-stock representation); we now want it
    # on sale at 1. Same number, different is_enabled — must still write.
    adapter = _RecordingEtsy(current_qty=1)
    adapter._current_qty = 1

    async def _authed_request(session, connection, method, path, **kwargs):
        adapter.calls.append((method, path))
        if method == "GET":
            return _FakeResponse(200, _inventory("SKU-1", 1, is_enabled=False))
        return _FakeResponse(200, {})

    adapter._authed_request = _authed_request

    await adapter.push_listing_quantity(None, None, _REF, "SKU-1", 1)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls
