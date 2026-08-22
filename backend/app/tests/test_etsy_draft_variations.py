"""Writing a variation matrix onto a newly-created Etsy draft.

Etsy's inventory write endpoint does not accept everything its own read endpoint returns,
and reports at most one invalid key per 400 — so those rules were found one rejection at a
time against a live listing while building push_listing_quantity. They are reproduced here
rather than rediscovered, and these tests exist so a future refactor can't quietly undo
them: the failure they prevent is a 400 that only appears against real Etsy, long after the
change that caused it.

The transport is faked; the payload is the thing under test.
"""

from decimal import Decimal

import pytest

from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.draft_listing import DraftPushError, build_draft, push_draft
from app.services.platforms.etsy import EtsyAdapter

ETSY = ListingPlatform.etsy


class FakeConnection:
    """Only the field the adapter reads. A real PlatformConnection would drag in token
    refresh, which the overridden transport never reaches."""

    external_account_id = "12345"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


class RecordingEtsy(EtsyAdapter):
    """A real EtsyAdapter with only the HTTP layer replaced, so the payload builders and
    every rule inside them are genuinely exercised."""

    def __init__(self, inventory_status=200):
        self.requests: list[tuple[str, str, dict]] = []
        self.inventory_status = inventory_status

    async def _authed_request(self, session, connection, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if path.endswith("/listings") and method == "POST":
            return FakeResponse(201, {"listing_id": 900001, "state": "draft"})
        if path.endswith("/images"):
            return FakeResponse(201, {})
        if path.endswith("/inventory") and method == "GET":
            # Etsy gives a new draft one default product; the readiness_state_id on its
            # offering is the only place to learn that required value.
            return FakeResponse(
                200,
                {"products": [{"sku": None, "offerings": [{"readiness_state_id": 77}]}]},
            )
        if path.endswith("/inventory") and method == "PUT":
            return FakeResponse(self.inventory_status, {})
        return FakeResponse(200, {})

    def inventory_body(self) -> dict:
        return next(
            kwargs["json"]
            for method, path, kwargs in self.requests
            if method == "PUT" and path.endswith("/inventory")
        )


async def _setup(session, *, variants, attributes=("Studs", "Colour"), stock=5):
    session.add(
        ListingProfile(
            platform=ETSY, name="Handmade", is_default=True, etsy_taxonomy_id=1234,
            etsy_who_made="i_did", etsy_when_made="made_to_order", etsy_is_supply=False,
            etsy_shipping_profile_id=99, etsy_readiness_state_id=7,
        )
    )
    names = list(attributes) + [None] * (3 - len(attributes))
    product = Product(
        name="Brick Pencil Pot", sku="SKU-0037", description="A pot.",
        sale_price=Decimal("12.50"), is_active=True, current_stock=stock,
        variant_attribute1_name=names[0], variant_attribute2_name=names[1],
        variant_attribute3_name=names[2],
    )
    session.add(product)
    await session.commit()

    for name, values, price, qty in variants:
        padded = list(values) + [None] * (3 - len(values))
        session.add(
            ProductVariant(
                product_id=product.id, variant_name=name, sku_suffix=name.replace(" ", "-").upper(),
                attribute1_value=padded[0], attribute2_value=padded[1], attribute3_value=padded[2],
                is_active=True, sale_price=price, current_stock=qty,
            )
        )
    await session.commit()
    return product


@pytest.mark.asyncio
async def test_a_single_unit_product_writes_no_inventory_at_all(session):
    """Nothing varies, so there is no matrix to set and no reason to risk the call."""
    product = await _setup(session, variants=[], attributes=())
    adapter = RecordingEtsy()

    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)
    assert not any(p.endswith("/inventory") for _, p, _ in adapter.requests)


@pytest.mark.asyncio
async def test_each_variant_becomes_a_product_with_its_own_sku_and_price(session):
    product = await _setup(
        session,
        variants=[
            ("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 3),
            ("6 Stud Teal", ("6 Stud", "Teal"), Decimal("14.95"), 2),
        ],
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    products = adapter.inventory_body()["products"]
    assert {p["sku"] for p in products} == {"SKU-0037-4-STUD-TEAL", "SKU-0037-6-STUD-TEAL"}
    # Price goes as a plain float, not the nested Money object the GET returns.
    assert {p["offerings"][0]["price"] for p in products} == {12.5, 14.95}
    assert all(isinstance(p["offerings"][0]["price"], float) for p in products)


@pytest.mark.asyncio
async def test_property_values_carry_the_name_and_omit_the_scale_name(session):
    """property_name is required on write; scale_name is rejected. Not symmetric, and both
    directions were confirmed against a real listing."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    values = adapter.inventory_body()["products"][0]["property_values"]
    assert [v["property_name"] for v in values] == ["Studs", "Colour"]
    assert [v["values"] for v in values] == [["4 Stud"], ["Teal"]]
    assert all("scale_name" not in v for v in values)


@pytest.mark.asyncio
async def test_no_rejected_write_keys_appear_anywhere_in_the_payload(session):
    """product_id, offering_id and is_deleted are all rejected on write — each one cost a
    round of live debugging to find."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    for entry in adapter.inventory_body()["products"]:
        assert "product_id" not in entry
        for offering in entry["offerings"]:
            assert "offering_id" not in offering
            assert "is_deleted" not in offering


@pytest.mark.asyncio
async def test_readiness_state_id_is_taken_from_the_draft_rather_than_invented(session):
    """It is required on write, and making one up would be guessing at a processing
    profile — so the GET happens first purely to learn it."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    assert adapter.inventory_body()["products"][0]["offerings"][0]["readiness_state_id"] == 77


@pytest.mark.asyncio
async def test_out_of_stock_is_off_sale_rather_than_quantity_zero(session):
    """Etsy refuses a literal 0 outright. quantity 1 with is_enabled false takes the
    offering off sale without deleting it or failing the push."""
    product = await _setup(
        session,
        variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 0)],
        stock=0,
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    offering = adapter.inventory_body()["products"][0]["offerings"][0]
    assert offering["quantity"] == 1
    assert offering["is_enabled"] is False


@pytest.mark.asyncio
async def test_every_property_is_declared_as_varying(session):
    """Etsy validates the supplied values against these arrays and rejects a mismatch.
    SKU, price and quantity are all per-unit here, so understating them is what produces a
    400."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    body = adapter.inventory_body()
    assert body["sku_on_property"] == [513, 514]
    assert body["price_on_property"] == [513, 514]
    assert body["quantity_on_property"] == [513, 514]


@pytest.mark.asyncio
async def test_one_attribute_uses_one_property_slot(session):
    product = await _setup(
        session, variants=[("Teal", ("Teal",), Decimal("12.50"), 1)], attributes=("Colour",)
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    assert adapter.inventory_body()["sku_on_property"] == [513]


@pytest.mark.asyncio
async def test_the_inventory_write_happens_after_the_image(session):
    """A rejected variation matrix should still leave a draft with its picture attached."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy()
    await push_draft(session, adapter, FakeConnection(), product.id, ETSY)

    order = [p for _, p, _ in adapter.requests]
    assert order.index("/shops/12345/listings") < len(order) - 1
    assert order[-1].endswith("/inventory")


@pytest.mark.asyncio
async def test_a_rejected_matrix_surfaces_rather_than_being_swallowed(session):
    """Unlike the image, a wrong inventory is worse than none — the seller would be
    looking at a listing whose variations don't match what they sell."""
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    adapter = RecordingEtsy(inventory_status=400)

    with pytest.raises(Exception) as excinfo:
        await push_draft(session, adapter, FakeConnection(), product.id, ETSY)
    assert "variations were rejected" in str(excinfo.value)


@pytest.mark.asyncio
async def test_three_attributes_are_refused_before_anything_is_sent(session):
    """Etsy accepts two. The limits matrix already treats a third as a hard blocker, so
    readiness stops this — but the point is that it stops it before any call, not after a
    listing exists."""
    product = await _setup(
        session,
        variants=[("A", ("4 Stud", "Teal", "Matte"), Decimal("12.50"), 1)],
        attributes=("Studs", "Colour", "Finish"),
    )
    adapter = RecordingEtsy()

    with pytest.raises(DraftPushError):
        await push_draft(session, adapter, FakeConnection(), product.id, ETSY)
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_attribute_names_reach_the_draft_in_slot_order(session):
    product = await _setup(
        session, variants=[("4 Stud Teal", ("4 Stud", "Teal"), Decimal("12.50"), 1)]
    )
    draft = await build_draft(session, product.id, ETSY)
    assert draft.attribute_names == ["Studs", "Colour"]
