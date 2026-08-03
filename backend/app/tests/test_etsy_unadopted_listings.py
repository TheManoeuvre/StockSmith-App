"""Coverage for the Etsy side: spotting listings StockSmith has no SKU for, and writing
its SKUs onto them.

The write path gets the same scrutiny as eBay's revise builder, for the same reason —
Etsy's inventory endpoint is a full replace, so anything the payload fails to echo back
is lost from a live listing. push_listing_quantity's docstring records how each of those
rules was found (one live 400 at a time); these tests pin them so the new writer can't
drift from the one that was actually verified.
"""

import pytest

from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.listing_adoption import find_unadopted_listings, known_stocksmith_skus
from app.services.platforms.base import ListingProductRef, UnadoptedListingCandidate
from app.services.platforms.etsy import EtsyAdapter
from app.services.platforms.errors import PlatformSyncError


def _raw_listing(listing_id: int, title: str, products: list[dict], state: str = "active") -> dict:
    return {
        "listing_id": listing_id,
        "title": title,
        "state": state,
        "inventory": {"products": products},
    }


def _raw_product(sku: str | None, colour: str | None = None, qty: int = 3, is_deleted: bool = False) -> dict:
    return {
        "product_id": 999,
        "sku": sku,
        "is_deleted": is_deleted,
        "property_values": (
            [
                {
                    "property_id": 200,
                    "property_name": "Colour",
                    "scale_id": None,
                    "scale_name": "should be dropped",
                    "value_ids": [1],
                    "values": [colour],
                }
            ]
            if colour
            else []
        ),
        "offerings": [
            {
                "offering_id": 555,
                "quantity": qty,
                "is_enabled": True,
                "is_deleted": False,
                "price": {"amount": 1250, "divisor": 100},
                "readiness_state_id": 7,
            }
        ],
    }


# --- Parsing ------------------------------------------------------------------------


def test_parse_listing_products_indexes_positionally():
    """Index is the addressing scheme for the write, and these listings' SKUs are by
    definition unreliable — so position has to survive parsing exactly."""
    listing = _raw_listing(1, "Mug", [_raw_product("A", "Black"), _raw_product(None, "White")])
    parsed = EtsyAdapter.parse_listing_products(listing)

    assert parsed.external_listing_id == "1"
    assert [p.index for p in parsed.products] == [0, 1]
    assert [p.sku for p in parsed.products] == ["A", None]
    assert parsed.products[0].variation == "Colour: Black"


def test_parse_skips_deleted_products():
    listing = _raw_listing(1, "Mug", [_raw_product("A"), _raw_product("B", is_deleted=True)])
    parsed = EtsyAdapter.parse_listing_products(listing)
    assert [p.sku for p in parsed.products] == ["A"]


def test_parse_empty_sku_string_becomes_none():
    """Etsy returns "" for an unset SKU; the report treats "no SKU" as a distinct state
    from "a SKU we don't recognise", so this normalisation matters."""
    parsed = EtsyAdapter.parse_listing_products(_raw_listing(1, "Mug", [_raw_product("")]))
    assert parsed.products[0].sku is None


# --- Which listings count as unadopted ----------------------------------------------


def _candidate(listing_id: str, skus: list[str | None]) -> UnadoptedListingCandidate:
    return UnadoptedListingCandidate(
        external_listing_id=listing_id,
        title="t",
        state="active",
        products=[ListingProductRef(index=i, sku=s, variation=None, quantity=1) for i, s in enumerate(skus)],
    )


def test_fully_matched_listing_is_not_reported():
    known = {"A", "B"}
    assert find_unadopted_listings([_candidate("1", ["A", "B"])], known) == []


def test_listing_with_no_skus_is_reported():
    assert len(find_unadopted_listings([_candidate("1", [None, None])], {"A"})) == 1


def test_partially_matched_listing_is_reported():
    """One variation linked and three not is exactly the gap worth surfacing — an
    all-or-nothing filter would hide it."""
    assert len(find_unadopted_listings([_candidate("1", ["A", None, None, None])], {"A"})) == 1


def test_unrecognised_sku_is_reported():
    assert len(find_unadopted_listings([_candidate("1", ["SOMETHING-ELSE"])], {"A"})) == 1


async def test_known_skus_uses_variant_full_skus_not_parent(session):
    """A product with variants sells under PARENT-SUFFIX, never the bare parent SKU —
    counting the parent would make every variant listing look adopted."""
    product = Product(name="Mount", sku="SKU-0012", current_stock=1, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add_all(
        [
            ProductVariant(product_id=product.id, variant_name="A", sku_suffix="A"),
            ProductVariant(product_id=product.id, variant_name="B", sku_suffix="B"),
        ]
    )
    plain = Product(name="Widget", sku="WIDGET", current_stock=1, allocated_qty=0)
    session.add(plain)
    await session.commit()

    known = await known_stocksmith_skus(session)

    assert known == {"SKU-0012-A", "SKU-0012-B", "WIDGET"}


async def test_known_skus_excludes_inactive(session):
    product = Product(name="Old", sku="RETIRED", current_stock=0, allocated_qty=0, is_active=False)
    session.add(product)
    await session.commit()
    assert await known_stocksmith_skus(session) == set()


# --- The inventory write ------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _RecordingAdapter(EtsyAdapter):
    """Captures the PUT body instead of sending it. Subclassing rather than mocking
    keeps the real update_listing_skus logic under test."""

    def __init__(self, inventory: dict):
        super().__init__("id", "secret")
        self._inventory = inventory
        self.put_body: dict | None = None

    async def _authed_request(self, session, connection, method, path, **kwargs):
        if method == "GET":
            return _FakeResponse(self._inventory)
        self.put_body = kwargs.get("json")
        return _FakeResponse({})


def _inventory(products: list[dict]) -> dict:
    return {
        "products": products,
        "price_on_property": [200],
        "quantity_on_property": [],
        "sku_on_property": [],
    }


async def test_write_sets_only_the_targeted_sku():
    inventory = _inventory([_raw_product("OLD-A", "Black"), _raw_product("OLD-B", "White")])
    adapter = _RecordingAdapter(inventory)

    await adapter.update_listing_skus(None, None, "1", {0: "NEW-A"})

    assert [p["sku"] for p in adapter.put_body["products"]] == ["NEW-A", "OLD-B"]


async def test_write_echoes_back_every_product():
    """Etsy's PUT is a full replace — an omitted product is removed from the listing."""
    inventory = _inventory([_raw_product("A"), _raw_product("B"), _raw_product("C")])
    adapter = _RecordingAdapter(inventory)

    await adapter.update_listing_skus(None, None, "1", {1: "NEW-B"})

    assert len(adapter.put_body["products"]) == 3


async def test_write_preserves_per_property_config():
    inventory = _inventory([_raw_product("A", "Black")])
    adapter = _RecordingAdapter(inventory)

    await adapter.update_listing_skus(None, None, "1", {0: "NEW"})

    assert adapter.put_body["price_on_property"] == [200]
    assert "quantity_on_property" in adapter.put_body
    assert "sku_on_property" in adapter.put_body


async def test_write_strips_and_keeps_the_right_property_keys():
    """scale_name is rejected by Etsy's write endpoint while property_name is required —
    asymmetric, and both directions were confirmed live (see _strip_property_value)."""
    adapter = _RecordingAdapter(_inventory([_raw_product("A", "Black")]))

    await adapter.update_listing_skus(None, None, "1", {0: "NEW"})

    pv = adapter.put_body["products"][0]["property_values"][0]
    assert "scale_name" not in pv
    assert pv["property_name"] == "Colour"
    assert "product_id" not in adapter.put_body["products"][0]


async def test_write_drops_offering_id_and_is_deleted():
    adapter = _RecordingAdapter(_inventory([_raw_product("A")]))

    await adapter.update_listing_skus(None, None, "1", {0: "NEW"})

    offering = adapter.put_body["products"][0]["offerings"][0]
    assert "offering_id" not in offering
    assert "is_deleted" not in offering
    assert offering["readiness_state_id"] == 7
    assert offering["price"] == 12.5


async def test_write_respects_etsy_quantity_floor_of_one():
    """Etsy rejects a literal 0 quantity outright (confirmed live). Renaming a SKU must
    not fail just because the variation happens to be out of stock."""
    adapter = _RecordingAdapter(_inventory([_raw_product("A", qty=0)]))

    await adapter.update_listing_skus(None, None, "1", {0: "NEW"})

    assert adapter.put_body["products"][0]["offerings"][0]["quantity"] == 1


class _PagingAdapter(EtsyAdapter):
    """fetch_all_listings duplicates build_listing_sku_index's pagination loop rather
    than sharing it (the hot path keeps its single pass). A hand-copied loop is exactly
    the kind of thing that silently stops after page one, so it gets its own coverage."""

    def __init__(self, pages: list[dict]):
        super().__init__("id", "secret")
        self._pages = pages
        self.offsets: list[int] = []

    async def _authed_request(self, session, connection, method, path, **kwargs):
        params = kwargs.get("params") or {}
        self.offsets.append(int(params.get("offset", 0)))
        return _FakeResponse(self._pages[len(self.offsets) - 1])


async def test_fetch_all_listings_paginates():
    from types import SimpleNamespace

    page1 = {"count": 3, "results": [_raw_listing(1, "A", []), _raw_listing(2, "B", [])]}
    page2 = {"count": 3, "results": [_raw_listing(3, "C", [])]}
    adapter = _PagingAdapter([page1, page2])

    listings = await adapter.fetch_all_listings(None, SimpleNamespace(external_account_id="shop"))

    assert [listing["listing_id"] for listing in listings] == [1, 2, 3]
    assert adapter.offsets == [0, 2]  # advanced by the number actually returned


async def test_fetch_all_listings_requires_a_shop_id():
    from types import SimpleNamespace

    with pytest.raises(PlatformSyncError, match="no shop id"):
        await EtsyAdapter("id", "s").fetch_all_listings(None, SimpleNamespace(external_account_id=None))


async def test_write_rejects_out_of_range_index():
    """Guards against acting on a stale picker: if the listing changed since it was
    loaded, an index could now point at a different variation — or none."""
    adapter = _RecordingAdapter(_inventory([_raw_product("A")]))

    with pytest.raises(PlatformSyncError, match="no product at index"):
        await adapter.update_listing_skus(None, None, "1", {5: "NEW"})

    assert adapter.put_body is None  # nothing written
