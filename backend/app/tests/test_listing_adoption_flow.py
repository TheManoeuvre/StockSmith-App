"""End-to-end coverage of the adoption endpoints, driven directly against the router
functions with a fake adapter — the same "drive the real pipeline in-process" approach
test_kitting_multiunit_override.py takes with order_sync, and for the same reason: these
endpoints orchestrate irreversible, money-adjacent marketplace calls, so the ordering and
the failure behaviour are the parts worth pinning down, not the HTTP plumbing.

The properties under test here are the ones that would damage a live shop if wrong:
  * SKUs are revised BEFORE migration (afterwards is effectively impossible — see
    EbayAdapter.revise_listing_skus).
  * A failed migration writes nothing locally, so a retry is clean.
  * An already-migrated listing is a successful no-op, so a partially-failed adoption
    can be re-run.
  * Etsy's Listing rows key on listing id, eBay's on SKU — swapping them would silently
    break every subsequent sync check and quantity push.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.models.variant import ProductVariant
from app.routers import platforms as platforms_router
from app.schemas.listing_adoption import (
    AdoptListingRequest,
    EtsyAdoptListingRequest,
    EtsyLinkChoice,
    VariationMappingChoice,
)
from app.services.platforms.base import (
    ClassicListingCandidate,
    ExternalListingRef,
    ListingProductRef,
    MigrationResult,
    UnadoptedListingCandidate,
)
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

_TRADING_SCOPE = "https://api.ebay.com/oauth/api_scope"


class FakeEbayAdapter:
    """Records every marketplace call in order, so tests can assert on sequencing —
    the revise-then-migrate ordering is the whole safety argument for phase 4."""

    def __init__(self, candidate: ClassicListingCandidate, migrated_skus: list[str] | None = None):
        self.candidate = candidate
        self.migrated_skus = migrated_skus if migrated_skus is not None else list(candidate.skus)
        self.calls: list[tuple] = []
        self.migrate_error: Exception | None = None
        self.index: dict[str, ExternalListingRef] = {}

    async def fetch_classic_listing_detail(self, session, connection, external_listing_id):
        self.calls.append(("detail", external_listing_id))
        return self.candidate

    async def build_listing_sku_index(self, session, connection, *, enrich=True, enrich_skus=None):
        # Records the enrichment hints so callers that shouldn't pay for per-SKU offer
        # lookups can be asserted on. Deliberately returns the whole index regardless:
        # these are fidelity hints, not filters, and a fake that narrowed the index would
        # hide exactly the bug that contract exists to prevent.
        self.calls.append(("index", enrich, enrich_skus))
        return self.index

    async def fetch_classic_listings(self, session, connection):
        return [self.candidate]

    async def revise_listing_skus(self, session, connection, candidate, new_skus, listing_sku=None):
        self.calls.append(("revise", list(new_skus), listing_sku))

    async def migrate_listing(self, session, connection, external_listing_id):
        self.calls.append(("migrate", external_listing_id))
        if self.migrate_error is not None:
            raise self.migrate_error
        return MigrationResult(external_listing_id=external_listing_id, inventory_item_skus=self.migrated_skus)


class FakeEtsyAdapter:
    def __init__(self, listings: list[dict] | None = None):
        self.listings = listings or []
        self.calls: list[tuple] = []
        self.write_error: Exception | None = None

    async def fetch_all_listings(self, session, connection):
        return self.listings

    async def update_listing_skus(self, session, connection, listing_id, sku_by_index):
        self.calls.append(("write_skus", listing_id, dict(sku_by_index)))
        if self.write_error is not None:
            raise self.write_error


def _use_adapter(monkeypatch, adapter, expected_type):
    """The router validates the adapter's concrete type, so the isinstance check has to
    be relaxed for a fake. Patching the class rather than the check keeps the real guard
    exercised everywhere else."""

    async def _get_adapter(session, platform, environment=None):
        return adapter

    monkeypatch.setattr(platforms_router, "get_adapter", _get_adapter)
    monkeypatch.setattr(platforms_router, expected_type, type(adapter))


async def _connect(session, platform: ListingPlatform, scopes: str | None = _TRADING_SCOPE) -> PlatformConnection:
    conn = PlatformConnection(
        platform=platform,
        access_token="token",
        refresh_token="refresh",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        external_account_id="shop-1",
        scopes=scopes,
    )
    session.add(conn)
    await session.commit()
    return conn


async def _make_product(session, sku: str, suffixes: list[str]) -> tuple[Product, list[ProductVariant]]:
    product = Product(name=f"Product {sku}", sku=sku, current_stock=5, allocated_qty=0, variant_attribute1_name="Colour")
    session.add(product)
    await session.flush()
    variants = []
    for i, suffix in enumerate(suffixes):
        variant = ProductVariant(
            product_id=product.id,
            variant_name=suffix,
            sku_suffix=suffix,
            attribute1_value=["Black", "White", "Red", "Blue"][i % 4],
        )
        session.add(variant)
        variants.append(variant)
    await session.commit()
    for v in variants:
        await session.refresh(v)
    return product, variants


def _candidate(
    skus: list[str],
    specifics: list[dict[str, str]] | None,
    is_migrated: bool = False,
    listing_sku: str | None = None,
):
    return ClassicListingCandidate(
        external_listing_id="227269664481",
        title="Aqara G400 mount",
        listing_type="FixedPriceItem",
        skus=skus,
        variation_specifics=specifics,
        quantity=4,
        is_migrated=is_migrated,
        listing_sku=listing_sku,
        detail_loaded=True,
    )


async def _listings_for(session, product_id: int, platform: ListingPlatform) -> list:
    rows = await session.execute(
        Listing.__table__.select().where(Listing.product_id == product_id, Listing.platform == platform)
    )
    return rows.all()


# --- eBay ---------------------------------------------------------------------------


async def test_align_skus_revises_before_migrating(session, monkeypatch):
    """The core safety property of phase 4. A classic listing's SKU is freely editable;
    a migrated one's is not, so getting this order backwards would leave the shop with
    permanently mismatched SKUs and no in-app way to fix them."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B"])
    adapter = FakeEbayAdapter(_candidate(["OLD-1", "OLD-2"], [{"Colour": "Black"}, {"Colour": "White"}]))
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[
                VariationMappingChoice(variant_id=variants[0].id, sku="OLD-1"),
                VariationMappingChoice(variant_id=variants[1].id, sku="OLD-2"),
            ],
            align_skus=True,
        ),
        session,
    )

    ordered = [c[0] for c in adapter.calls]
    assert ordered.index("revise") < ordered.index("migrate")
    revise_call = next(c for c in adapter.calls if c[0] == "revise")
    assert revise_call[1] == ["SKU-0012-A", "SKU-0012-B"]
    # The listing had no Item.SKU; alignment sets it from the product's own SKU so eBay's
    # migration doesn't reject the multi-variation listing.
    assert revise_call[2] == "SKU-0012"

    # Alignment resolved the divergence, so nothing should be reported as conflicting.
    assert result.skus_aligned is True
    assert [u.sku_conflict for u in result.units] == [False, False]


async def test_align_skus_is_a_noop_when_skus_already_match(session, monkeypatch):
    """Revising is a real edit to a live listing — it must not fire when there's nothing
    to change, even with align_skus on."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B"])
    adapter = FakeEbayAdapter(
        _candidate(
            ["SKU-0012-A", "SKU-0012-B"],
            [{"Colour": "Black"}, {"Colour": "White"}],
            listing_sku="SKU-0012",
        )
    )
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[
                VariationMappingChoice(variant_id=variants[0].id, sku="SKU-0012-A"),
                VariationMappingChoice(variant_id=variants[1].id, sku="SKU-0012-B"),
            ],
            align_skus=True,
        ),
        session,
    )

    assert not any(c[0] == "revise" for c in adapter.calls)
    assert result.skus_aligned is False


async def test_multi_variation_without_listing_sku_is_blocked_with_a_useful_message(session, monkeypatch):
    """eBay rejects a multi-variation migration when the listing itself has no Item.SKU
    ("The listing SKU cannot be null or empty"). Without align_skus there's nothing to set
    it, so this must fail early with guidance rather than surfacing eBay's bare 400."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B"])
    adapter = FakeEbayAdapter(
        _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}], listing_sku=None)
    )
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_ebay_listing(
            product.id,
            AdoptListingRequest(
                external_listing_id="227269664481",
                variation_mapping=[
                    VariationMappingChoice(variant_id=variants[0].id, sku="SKU-0012-A"),
                    VariationMappingChoice(variant_id=variants[1].id, sku="SKU-0012-B"),
                ],
                align_skus=False,
            ),
            session,
        )

    assert exc.value.status_code == 400
    assert "listing-level SKU" in exc.value.detail
    assert not any(c[0] == "migrate" for c in adapter.calls)
    assert await _listings_for(session, product.id, ListingPlatform.ebay) == []


async def test_multi_variation_missing_listing_sku_is_fixed_by_align_skus(session, monkeypatch):
    """With align_skus on, the same listing migrates: alignment sets Item.SKU from the
    product's own SKU first, so eBay's migration accepts it. This is the reported bug."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B"])
    adapter = FakeEbayAdapter(
        _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}], listing_sku=None)
    )
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[
                VariationMappingChoice(variant_id=variants[0].id, sku="SKU-0012-A"),
                VariationMappingChoice(variant_id=variants[1].id, sku="SKU-0012-B"),
            ],
            align_skus=True,
        ),
        session,
    )

    revise_call = next(c for c in adapter.calls if c[0] == "revise")
    assert revise_call[1] == ["SKU-0012-A", "SKU-0012-B"]  # variations unchanged, all echoed
    assert revise_call[2] == "SKU-0012"  # Item.SKU set from the product SKU
    assert [c[0] for c in adapter.calls].index("revise") < [c[0] for c in adapter.calls].index("migrate")
    assert len(result.units) == 2


async def test_without_align_skus_conflict_is_reported_and_stockssmith_sku_wins(session, monkeypatch):
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A"])
    adapter = FakeEbayAdapter(_candidate(["EBAY-OWN-SKU"], None))
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[VariationMappingChoice(variant_id=variants[0].id, sku="EBAY-OWN-SKU")],
            align_skus=False,
        ),
        session,
    )

    assert not any(c[0] == "revise" for c in adapter.calls)
    assert result.units[0].sku_conflict is True
    assert result.units[0].expected_sku == "SKU-0012-A"
    assert result.units[0].actual_sku == "EBAY-OWN-SKU"

    rows = await _listings_for(session, product.id, ListingPlatform.ebay)
    assert rows[0].external_listing_id == "SKU-0012-A"  # StockSmith's own, never eBay's


async def test_failed_migration_writes_nothing_locally(session, monkeypatch):
    """A retry after a failure must start from a clean slate — a half-written Listing
    row would make the product look linked when eBay has no such object."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A"])
    adapter = FakeEbayAdapter(_candidate(["SKU-0012-A"], None))
    adapter.migrate_error = PlatformSyncError("eBay rejected migration")
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_ebay_listing(
            product.id,
            AdoptListingRequest(
                external_listing_id="227269664481",
                variation_mapping=[VariationMappingChoice(variant_id=variants[0].id, sku="SKU-0012-A")],
            ),
            session,
        )

    assert exc.value.status_code == 502
    assert await _listings_for(session, product.id, ListingPlatform.ebay) == []


async def test_adopt_rejects_empty_mapping(session, monkeypatch):
    await _connect(session, ListingPlatform.ebay)
    product, _ = await _make_product(session, "SKU-0012", ["A"])
    adapter = FakeEbayAdapter(_candidate(["SKU-0012-A"], None))
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_ebay_listing(
            product.id, AdoptListingRequest(external_listing_id="1", variation_mapping=[]), session
        )
    assert exc.value.status_code == 400


async def test_adopt_rejects_duplicate_sku_mapping(session, monkeypatch):
    """Two variants claiming one eBay SKU would leave two Listing rows fighting over the
    same marketplace object on every quantity push."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B"])
    adapter = FakeEbayAdapter(_candidate(["SHARED"], None))
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_ebay_listing(
            product.id,
            AdoptListingRequest(
                external_listing_id="227269664481",
                variation_mapping=[
                    VariationMappingChoice(variant_id=variants[0].id, sku="SHARED"),
                    VariationMappingChoice(variant_id=variants[1].id, sku="SHARED"),
                ],
            ),
            session,
        )

    assert exc.value.status_code == 400
    assert adapter.calls == []  # rejected before any marketplace call


async def test_etsy_adopt_rejects_duplicate_variation_link(session, monkeypatch):
    await _connect(session, ListingPlatform.etsy)
    product, variants = await _make_product(session, "WIDGET", ["A", "B"])
    adapter = FakeEtsyAdapter()
    _use_adapter(monkeypatch, adapter, "EtsyAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_etsy_listing(
            product.id,
            EtsyAdoptListingRequest(
                external_listing_id="1",
                links=[
                    EtsyLinkChoice(variant_id=variants[0].id, product_index=0),
                    EtsyLinkChoice(variant_id=variants[1].id, product_index=0),
                ],
            ),
            session,
        )

    assert exc.value.status_code == 400
    assert adapter.calls == []


async def test_etsy_adopt_without_write_skus_still_links(session, monkeypatch):
    """write_skus=False is the "the SKU is already right, just link it" case — it must
    not call Etsy at all, but must still create the local rows."""
    await _connect(session, ListingPlatform.etsy)
    product, variants = await _make_product(session, "WIDGET", ["A"])
    adapter = FakeEtsyAdapter()
    _use_adapter(monkeypatch, adapter, "EtsyAdapter")

    await platforms_router.adopt_etsy_listing(
        product.id,
        EtsyAdoptListingRequest(
            external_listing_id="1234567890",
            links=[EtsyLinkChoice(variant_id=variants[0].id, product_index=0)],
            write_skus=False,
        ),
        session,
    )

    assert adapter.calls == []
    rows = await _listings_for(session, product.id, ListingPlatform.etsy)
    assert rows[0].external_listing_id == "1234567890"


async def test_missing_trading_scope_is_a_clear_400(session, monkeypatch):
    """A connection made before the Trading scope was requested still passes every other
    health check in the app, so without this it would fail deep inside eBay's API with
    an opaque error."""
    await _connect(session, ListingPlatform.ebay, scopes="https://api.ebay.com/oauth/api_scope/sell.inventory")
    product, _ = await _make_product(session, "SKU-0012", ["A"])

    with pytest.raises(HTTPException) as exc:
        await platforms_router.get_product_unmigrated_listings(product.id, session)

    assert exc.value.status_code == 400
    assert "Reconnect eBay" in exc.value.detail


async def test_sell_inventory_scope_alone_does_not_satisfy_the_base_scope(session):
    """Guards the exact-token check in _has_trading_scope: every Sell scope string starts
    with the base scope, so a naive substring test would wrongly pass here."""
    conn = PlatformConnection(
        platform=ListingPlatform.ebay,
        scopes="https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.finances",
    )
    assert platforms_router._has_trading_scope(conn) is False

    conn.scopes = "https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope"
    assert platforms_router._has_trading_scope(conn) is True


@pytest.mark.parametrize("scopes", [None, "", "   "])
async def test_unrecorded_scopes_fail_open(session, scopes):
    """Regression, found in production: eBay's token endpoint returns no `scope` field,
    so connection.scopes was NULL for every eBay connection ever made. Reading NULL as
    "scope missing" latched the reconnect banner on permanently — reconnecting could
    never clear it, because the reconnect wrote NULL right back.

    Unknown must therefore mean "let the API decide", not "definitely broken". This is a
    diagnostic to improve an error message, never a security gate.
    """
    conn = PlatformConnection(platform=ListingPlatform.ebay, scopes=scopes)
    assert platforms_router._has_trading_scope(conn) is True


async def test_unrecorded_scopes_do_not_block_the_picker(session, monkeypatch):
    """The user-visible half of the bug above: the endpoint must work on a connection
    whose scopes were never recorded."""
    await _connect(session, ListingPlatform.ebay, scopes=None)
    product, _ = await _make_product(session, "SKU-0012", ["A"])
    adapter = FakeEbayAdapter(_candidate(["SKU-0012-A"], None))
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    report = await platforms_router.get_product_unmigrated_listings(product.id, session)

    assert report.total_count == 1


async def test_trading_auth_failure_carries_the_reconnect_hint():
    """With the pre-check failing open, a genuinely missing scope now surfaces as eBay's
    own 401 — which says nothing actionable on its own."""
    mapped = platforms_router._map_trading_error(PlatformAuthError("Invalid access token"))

    assert mapped.status_code == 401
    assert "Reconnect eBay" in mapped.detail
    assert "Invalid access token" in mapped.detail


async def test_non_auth_trading_errors_are_not_given_the_hint():
    """A rate limit or a parse failure has nothing to do with scopes — attaching the
    reconnect advice there would send the user chasing the wrong problem."""
    mapped = platforms_router._map_trading_error(PlatformRateLimitError("slow down"))

    assert mapped.status_code == 429
    assert "Reconnect eBay" not in mapped.detail


async def test_already_migrated_listing_is_not_revised(session, monkeypatch):
    """Its SKUs can't be safely rewritten any more, so alignment must be skipped rather
    than attempted — revise_listing_skus would raise on a migrated candidate."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A"])
    adapter = FakeEbayAdapter(_candidate(["EBAY-OWN"], None, is_migrated=True))
    adapter.index = {
        "EBAY-OWN": ExternalListingRef(
            external_listing_id="EBAY-OWN", title="t", sku="EBAY-OWN", state="active", quantity=1, variation=None
        )
    }
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[VariationMappingChoice(variant_id=variants[0].id, sku="EBAY-OWN")],
            align_skus=True,
        ),
        session,
    )

    assert not any(c[0] == "revise" for c in adapter.calls)
    assert result.skus_aligned is False
    assert result.units[0].sku_conflict is True


async def test_four_variation_listing_writes_one_row_per_variant(session, monkeypatch):
    """The SKU-0012 case this feature exists for."""
    await _connect(session, ListingPlatform.ebay)
    product, variants = await _make_product(session, "SKU-0012", ["A", "B", "C", "D"])
    specifics = [{"Colour": c} for c in ("Black", "White", "Red", "Blue")]
    adapter = FakeEbayAdapter(
        _candidate([f"SKU-0012-{s}" for s in "ABCD"], specifics, listing_sku="SKU-0012")
    )
    _use_adapter(monkeypatch, adapter, "EbayAdapter")

    result = await platforms_router.adopt_ebay_listing(
        product.id,
        AdoptListingRequest(
            external_listing_id="227269664481",
            variation_mapping=[
                VariationMappingChoice(variant_id=v.id, sku=f"SKU-0012-{s}") for v, s in zip(variants, "ABCD")
            ],
        ),
        session,
    )

    assert len(result.units) == 4
    assert not any(u.sku_conflict for u in result.units)
    rows = await _listings_for(session, product.id, ListingPlatform.ebay)
    assert len(rows) == 4
    assert {r.external_listing_id for r in rows} == {f"SKU-0012-{s}" for s in "ABCD"}


# --- Etsy ---------------------------------------------------------------------------


async def test_etsy_adopt_writes_listing_id_not_sku(session, monkeypatch):
    """Etsy's index keys Listing.external_listing_id on the listing id while eBay's keys
    it on the SKU (see EtsyAdapter._index_listing_skus vs
    EbayAdapter._index_inventory_item). Writing eBay's convention here would make every
    later sync check report the listing as missing."""
    await _connect(session, ListingPlatform.etsy)
    product, variants = await _make_product(session, "WIDGET", ["A"])
    adapter = FakeEtsyAdapter()
    _use_adapter(monkeypatch, adapter, "EtsyAdapter")

    await platforms_router.adopt_etsy_listing(
        product.id,
        EtsyAdoptListingRequest(
            external_listing_id="1234567890",
            links=[EtsyLinkChoice(variant_id=variants[0].id, product_index=0)],
        ),
        session,
    )

    rows = await _listings_for(session, product.id, ListingPlatform.etsy)
    assert rows[0].external_listing_id == "1234567890"
    assert adapter.calls == [("write_skus", "1234567890", {0: "WIDGET-A"})]


async def test_etsy_adopt_refuses_product_without_sku(session, monkeypatch):
    """StockSmith's SKU is what gets written to Etsy, so there has to be one — otherwise
    this would blank the listing's existing SKU and make things worse."""
    await _connect(session, ListingPlatform.etsy)
    product = Product(name="No SKU product", sku=None, current_stock=1, allocated_qty=0)
    session.add(product)
    await session.commit()
    adapter = FakeEtsyAdapter()
    _use_adapter(monkeypatch, adapter, "EtsyAdapter")

    with pytest.raises(HTTPException) as exc:
        await platforms_router.adopt_etsy_listing(
            product.id,
            EtsyAdoptListingRequest(
                external_listing_id="1", links=[EtsyLinkChoice(variant_id=None, product_index=0)]
            ),
            session,
        )

    assert exc.value.status_code == 400
    assert adapter.calls == []  # nothing written to Etsy


async def test_etsy_write_failure_writes_nothing_locally(session, monkeypatch):
    await _connect(session, ListingPlatform.etsy)
    product, variants = await _make_product(session, "WIDGET", ["A"])
    adapter = FakeEtsyAdapter()
    adapter.write_error = PlatformSyncError("Etsy rejected the inventory write")
    _use_adapter(monkeypatch, adapter, "EtsyAdapter")

    with pytest.raises(HTTPException):
        await platforms_router.adopt_etsy_listing(
            product.id,
            EtsyAdoptListingRequest(
                external_listing_id="1", links=[EtsyLinkChoice(variant_id=variants[0].id, product_index=0)]
            ),
            session,
        )

    assert await _listings_for(session, product.id, ListingPlatform.etsy) == []
