from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.models.platform_connection import PlatformConnection


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
