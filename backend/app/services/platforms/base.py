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
