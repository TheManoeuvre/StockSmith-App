import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.models.platform_connection import PlatformConnection


class PaymentState(str, enum.Enum):
    """Whether the marketplace has actually taken the buyer's money yet — the single
    signal order_sync gates imports on. Deliberately NOT a mirror of any one platform's
    status field: Etsy and eBay express this completely differently, and Etsy's own
    Payments `status` string is undocumented free-form (see etsy._receipt_payment_state).
    Adapters normalise into these three states; order_sync owns what they mean."""

    # Money has actually been received by the seller.
    settled = "settled"
    # Not received: pending, processing, failed, or simply unknown. This is the
    # fail-closed default — an adapter that forgets to set payment_state imports
    # nothing rather than importing everything.
    unsettled = "unsettled"
    # Was received, then fully returned (refund or chargeback).
    reversed = "reversed"


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite doesn't have a real timezone-aware column type — SQLAlchemy's
    DateTime(timezone=True) accepts an aware datetime on write but doesn't reliably
    round-trip the tzinfo back on read (confirmed live: a freshly-written
    access_token_expires_at came back naive, blowing up the `datetime.now(timezone.utc)
    >= connection.access_token_expires_at` comparison both adapters' _ensure_fresh do
    with `TypeError: can't compare offset-naive and offset-aware datetimes`). Every
    datetime this app ever writes is UTC (see TokenSet.expires_at's own construction), so
    a naive value read back is safe to assume is UTC and re-attach tzinfo to."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: str | None = None


@dataclass
class ExternalOrderLine:
    external_line_id: str
    sku: str | None
    qty: int
    unit_price: str | None
    currency: str | None


@dataclass
class ExternalOrder:
    external_order_id: str
    buyer_name: str | None
    buyer_note: str | None
    placed_at: datetime
    # When the marketplace last touched this order (shipment, cancellation, etc.) — the
    # value order_sync advances its sync watermark to, so a receipt that stops changing
    # keeps satisfying future min_last_modified fetches instead of aging out of the query
    # window forever.
    last_modified: datetime
    is_cancelled: bool
    is_shipped: bool
    lines: list[ExternalOrderLine] = field(default_factory=list)
    # The untouched marketplace response this was parsed from — carried through so a
    # preview/debug view can show ground truth alongside our interpretation of it. Cheap
    # insurance against the parsing logic guessing a field name wrong.
    raw: dict = field(default_factory=dict)

    # Buyer-facing totals off the receipt — always available. Money values as decimal
    # strings (same convention as ExternalOrderLine.unit_price).
    currency: str | None = None
    grand_total: str | None = None
    subtotal: str | None = None
    shipping_charged: str | None = None
    tax_charged: str | None = None
    vat_charged: str | None = None
    discount_amount: str | None = None
    refunded_amount: str | None = None

    # The marketplace's own payment breakdown — a separate call, may be unavailable
    # (None) if the order's payment hasn't settled yet.
    payment_fees: str | None = None
    payment_net: str | None = None
    payment_status: str | None = None

    # Whether the buyer's money has actually landed. Defaults to `unsettled` on purpose:
    # order_sync refuses to import an unknown order in this state, so a new adapter — or
    # a parse path that forgets to set this — fails closed (imports nothing) rather than
    # open (imports everything, reserves stock, and pushes reduced quantities to the live
    # marketplace listings). See order_sync._partition_new_orders.
    payment_state: PaymentState = PaymentState.unsettled

    # False when the adapter deliberately skipped the per-order enrichment call that
    # populates the payment_fees/payment_net/payment_status trio directly above (Etsy's
    # Payments + ledger endpoints, eBay's Finances API). That happens for orders
    # re-fetched only because the unpaid hold widened the query window, and for unsettled
    # orders that have no payment record to fetch yet — re-enriching those on every poll
    # would multiply API cost against a fixed daily quota for no new information.
    #
    # Scope is exactly those three fields: every other money field on this dataclass
    # comes straight off the list response and is always accurate. Consumers MUST treat
    # the trio as "not fetched" rather than "known to be empty" — see
    # order_sync._apply_financials, which skips only those three when this is False.
    # Without that, a held-open re-fetch would null out a payment breakdown an earlier
    # sync had already stored correctly.
    financials_enriched: bool = True


@dataclass
class ExternalListingRef:
    external_listing_id: str
    # None when the marketplace didn't return a title — deliberately NOT coerced to "".
    # eBay's getInventoryItems (plural) is known to omit the whole `product` container for
    # some items that getInventoryItem (singular) returns in full, and an empty string
    # would sail past the UI's `?? "—"` placeholder and render as a blank cell.
    title: str | None
    sku: str | None
    state: str
    quantity: int
    # Human-readable property values for this specific SKU/offering within the listing
    # (e.g. "Colour: Caramel" or "Size: Large, Colour: Caramel") — lets a human confirm
    # the right Etsy variation was matched to the right StockSmith variant, since several
    # variants often share one listing (`title`) and only differ by this.
    variation: str | None


@dataclass
class ClassicListingCandidate:
    """A classic (possibly-unmigrated) eBay listing surfaced via the Trading API's
    GetSellerList — one per ItemID, not one per SKU, since a classic listing may carry
    zero, one, or (multi-variation) several SKUs. eBay-only: Etsy has no equivalent
    classic/Inventory-API split, so EtsyAdapter never produces these."""

    external_listing_id: str
    title: str
    listing_type: str
    skus: list[str]
    variation_specifics: list[dict[str, str]] | None
    quantity: int
    is_migrated: bool
    # The listing-level Item.SKU, distinct from the per-variation SKUs in `skus`. For a
    # single-SKU listing it is the same value as skus[0]; for a multi-variation listing
    # it is a separate field that a seller often leaves unset. eBay's bulkMigrateListing
    # rejects a multi-variation listing with no Item.SKU ("The listing SKU cannot be null
    # or empty", errorId 25002) even though its own migration docs only mention the
    # per-variation ones — confirmed live. None when the listing carries no Item.SKU.
    listing_sku: str | None = None
    ineligibility_reasons: list[str] = field(default_factory=list)
    # False when this came from a bulk list call that doesn't return per-variation
    # detail (eBay's GetMyeBaySelling ActiveList), True when it came from a per-item
    # detail call (GetItem). Matters because an empty `skus` means two completely
    # different things in those two cases — "this listing genuinely has no SKU" versus
    # "the list call simply didn't tell us" — and only the former is a real
    # ineligibility. Eligibility evaluation must not assert a missing SKU while this is
    # False; see ebay._evaluate_eligibility.
    detail_loaded: bool = False


@dataclass
class ListingProductRef:
    """One sellable product within a marketplace listing — an Etsy listing's per-SKU
    "product" entry. `index` is its position in the listing's own products array, which
    is the only stable way to address a product that has no SKU yet (precisely the case
    the unadopted-listing flow exists to fix)."""

    index: int
    sku: str | None
    variation: str | None
    quantity: int


@dataclass
class UnadoptedListingCandidate:
    """A live marketplace listing that StockSmith has no matching SKU for — i.e. a gap
    in StockSmith's own catalog rather than a marketplace-visibility problem.

    Distinct from ClassicListingCandidate, which is the opposite situation (StockSmith
    knows about the product; the marketplace API can't see the listing until it's
    migrated). Etsy only ever produces these, since every Etsy listing is visible
    through its one listings endpoint and there is no migration step."""

    external_listing_id: str
    title: str
    state: str
    products: list[ListingProductRef]


@dataclass
class MigrationResult:
    """The outcome of eBay's bulkMigrateListing for one classic listing — the SKU(s)
    confirmed present in the Inventory API afterward, read back so the caller can
    detect a SKU StockSmith didn't expect (see listing_adoption.py's conflict check)."""

    external_listing_id: str
    inventory_item_skus: list[str]
    raw: dict = field(default_factory=dict)


@dataclass
class DraftImage:
    """An image to attach to a new listing, as bytes.

    Bytes rather than a path or URL because StockSmith is a desktop app with local file
    storage — there is no publicly reachable URL for an asset, and a marketplace that wants
    one (eBay) has to be handed the bytes and given somewhere to put them first.
    """

    data: bytes
    filename: str
    rank: int = 1


@dataclass
class DraftUnit:
    """One sellable thing within a draft — a variant, or the product itself when it has
    none. Mirrors how listing_sync partitions a product into checkable units, so the two
    can't disagree about what a unit is."""

    sku: str | None
    price: str
    quantity: int
    # Attribute name -> value, e.g. {"Colour": "Teal"}. Empty for a product with no variants.
    attributes: dict[str, str] = field(default_factory=dict)
    # Round-trips so the caller can write Listing rows without re-deriving which unit is which.
    variant_id: int | None = None


@dataclass
class DraftListing:
    """Everything needed to create one draft listing, in marketplace-neutral terms.

    One object per product rather than per unit: both marketplaces model a multi-variation
    listing as a single listing covering all of them.

    `metadata` is deliberately an opaque bag keyed by well-known names ("etsy.taxonomy_id",
    "ebay.category_id") rather than typed fields. It keeps this module free of either
    marketplace's vocabulary — the same reasoning that keeps OAuth scope policy out of the
    adapters. An adapter reads the keys it needs and ignores the rest; a missing required
    key is caught by the readiness check before the call, never by the adapter.
    """

    title: str
    description: str
    currency: str
    # Attribute names in slot order, so an adapter can map them onto whatever the
    # marketplace calls a variation without re-deriving the order from the units.
    attribute_names: list[str] = field(default_factory=list)
    units: list[DraftUnit] = field(default_factory=list)
    images: list[DraftImage] = field(default_factory=list)
    metadata: dict[str, str | int | bool] = field(default_factory=dict)


@dataclass
class DraftListingResult:
    """What a marketplace gave back after creating a draft.

    `unit_refs` maps variant id (as a string, None for the product itself) to whatever
    belongs in Listing.external_listing_id for that unit. The adapter decides, because the
    two marketplaces mean different things by that column — Etsy the listing id, eBay the
    SKU — and having each adapter state it removes the caller's need to know, which is
    where that overloading currently causes trouble.

    `publish_blockers` are things the marketplace will refuse at publish time but tolerated
    at create time, e.g. an Etsy draft with no image.
    """

    external_listing_id: str | None
    state: str
    unit_refs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    publish_blockers: list[str] = field(default_factory=list)


class PlatformAdapter(Protocol):
    """Everything the allocation/sync/push services need from a marketplace, kept
    Etsy-agnostic so eBay/Shopify can be added later without touching core logic —
    only a new adapter + registry entry."""

    def build_authorize_url(self, state: str, code_challenge: str, redirect_uri: str, scopes: list[str]) -> str: ...

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet: ...

    async def refresh(self, refresh_token: str) -> TokenSet: ...

    async def fetch_account_id(self, access_token: str) -> str: ...

    async def fetch_orders_since(
        self, session, connection: PlatformConnection, since: datetime | None
    ) -> list[ExternalOrder]: ...

    async def push_listing_quantity(
        self, session, connection: PlatformConnection, listing_ref: ExternalListingRef, sku: str | None, qty: int
    ) -> None: ...

    async def create_draft_listing(
        self, session, connection: PlatformConnection, draft: DraftListing
    ) -> DraftListingResult: ...
    """Creates an unpublished listing from StockSmith's own data.

    On the Protocol rather than adapter-specific for the same reason push_listing_quantity
    is: a genuinely shared capability with different mechanics behind it. (migrate_listing
    and fetch_classic_listings live off-Protocol because they are eBay-only *concepts* —
    Etsy has no migration.)

    Must never publish. Etsy has a real draft state; eBay does not, and simulates one with
    an inventory item plus an unpublished offer. Either way the seller finishes and
    publishes in the marketplace's own editor, so nothing here can put an item on sale."""

    async def build_listing_sku_index(
        self,
        session,
        connection: PlatformConnection,
        *,
        enrich: bool = True,
        enrich_skus: set[str] | None = None,
    ) -> dict[str, ExternalListingRef]: ...
    """`enrich`/`enrich_skus` are fidelity hints, not filters: the index must always
    contain every SKU the marketplace reports, whatever they're set to — callers rely on
    it for membership tests. They exist because some adapters need extra per-SKU calls to
    fill a ref completely (eBay's listing state lives on a separate Offer object), and a
    caller that only needs membership shouldn't pay for them. An adapter whose single
    crawl already returns full fidelity should accept and ignore both."""
