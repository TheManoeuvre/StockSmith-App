from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.models.platform_connection import PlatformConnection


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


@dataclass
class ExternalListingRef:
    external_listing_id: str
    title: str
    sku: str | None
    state: str
    quantity: int
    # Human-readable property values for this specific SKU/offering within the listing
    # (e.g. "Colour: Caramel" or "Size: Large, Colour: Caramel") — lets a human confirm
    # the right Etsy variation was matched to the right StockSmith variant, since several
    # variants often share one listing (`title`) and only differ by this.
    variation: str | None


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

    async def build_listing_sku_index(
        self, session, connection: PlatformConnection
    ) -> dict[str, ExternalListingRef]: ...
