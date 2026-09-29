"""SquareAdapter — pulls in-person Square orders into StockSmith's order-sync pipeline.

Square is a fundamentally narrower fit for PlatformAdapter than Etsy/eBay: there is no
OAuth flow (a pasted personal access token instead — see routers/platforms.connect_square
and square_client.py) and no listing management at all (Square products are StockSmith
catalogue products synced the normal SKU/BOM way, not pushed listings — see
docs/plan-square-integration.md's "custom items vs catalogue" decision). Only
fetch_orders_since does real work here; every other Protocol method is unreachable for
Square today and raises NotImplementedError so a future call site that assumes otherwise
fails loudly rather than doing something wrong silently.

Not yet wired into services.platforms.get_adapter() or the sync scheduler — see the plan
doc's "Registry + sync wiring" phase. This adapter is usable today only by constructing it
directly (as its tests do).
"""

from datetime import datetime, timezone

from app.models.order import OrderFulfilmentMethod
from app.models.platform_connection import PlatformConnection
from app.services.platforms import square_client
from app.services.platforms.base import (
    DraftListing,
    DraftListingResult,
    ExternalListingRef,
    ExternalOrder,
    ExternalOrderLine,
    PaymentState,
    ensure_utc,
)
from app.services.platforms.errors import PlatformSyncError

# Case-insensitive match against a line item's name. Recognising it here means it's
# excluded from ExternalOrder.lines (it isn't a product — see fulfilment_method below,
# which already comes from the order's own fulfillments, not from this line). Hardcoded
# for now; docs/plan-square-integration.md's Settings phase makes this configurable.
_DELIVERY_LINE_NAME = "delivery"

_PAGE_LIMIT = 100
_MAX_PAGES = 20


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_money(money: dict | None) -> str | None:
    """Square money objects are minor-unit integers ({"amount": 1850, "currency": "GBP"}
    means £18.50) — unlike Etsy/eBay's decimal-string Money objects, so this divides by
    100 rather than just formatting."""
    if not money or money.get("amount") is None:
        return None
    return f"{money['amount'] / 100:.2f}"


def _parse_fulfilment(fulfillments: list[dict]) -> tuple[OrderFulfilmentMethod | None, datetime | None]:
    """Only the first fulfilment is read — every order the spike produced had exactly
    one, and StockSmith's model (one fulfilment_method per order) has nowhere to put a
    second one anyway. PICKUP's pickup_at becomes collect_by; SHIPMENT has no date field
    at all to read (confirmed by the spike), so collect_by stays None for it."""
    if not fulfillments:
        return None, None
    fulfilment = fulfillments[0]
    kind = fulfilment.get("type")
    if kind == "PICKUP":
        pickup_at = _parse_timestamp((fulfilment.get("pickup_details") or {}).get("pickup_at"))
        return OrderFulfilmentMethod.collect, pickup_at
    if kind == "SHIPMENT":
        return OrderFulfilmentMethod.delivery, None
    return None, None


def _payment_state(order: dict, is_cancelled: bool) -> PaymentState:
    """Settlement is net_amount_due_money hitting zero, NOT order.state — the sandbox spike
    found a fully-paid order's state stays "OPEN" forever (see the plan doc's spike
    findings). A cancelled order needs its own branch: the same spike found a cancelled,
    never-paid order still reports a nonzero net_amount_due, which would otherwise read as
    "awaiting payment" rather than "cancelled".

    The reversed case below (a cancelled order that WAS paid) is an assumption, not
    something the spike exercised — its cancelled test order was never paid. Square
    requires refunding a paid order before/while cancelling it, so the presence of a
    tender is used as a proxy for "this was paid at some point"; verify against a real
    paid-then-cancelled sandbox order before trusting this in production.
    """
    if is_cancelled:
        return PaymentState.reversed if order.get("tenders") else PaymentState.unsettled
    net_due = (order.get("net_amount_due_money") or {}).get("amount", 0)
    return PaymentState.settled if net_due == 0 else PaymentState.unsettled


class SquareAdapter:
    """See module docstring — orders only, nothing else implemented."""

    # ---- Not supported for Square: no OAuth, no listing management ----

    def build_authorize_url(self, state: str, code_challenge: str, redirect_uri: str, scopes: list[str]) -> str:
        raise NotImplementedError(
            "Square is connected with a pasted access token, not OAuth — see routers/platforms.connect_square"
        )

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str):
        raise NotImplementedError(
            "Square is connected with a pasted access token, not OAuth — see routers/platforms.connect_square"
        )

    async def refresh(self, refresh_token: str):
        raise NotImplementedError("Square access tokens don't expire or refresh")

    async def fetch_account_id(self, access_token: str) -> str:
        raise NotImplementedError(
            "Square's 'account' is the chosen location, set via routers/platforms.set_square_location"
        )

    async def push_listing_quantity(
        self, session, connection: PlatformConnection, listing_ref: ExternalListingRef, sku: str | None, qty: int
    ) -> None:
        raise NotImplementedError(
            "Square products are StockSmith catalogue products, not pushed listings — see "
            "docs/plan-square-integration.md"
        )

    async def create_draft_listing(
        self, session, connection: PlatformConnection, draft: DraftListing
    ) -> DraftListingResult:
        raise NotImplementedError("Square has no listing drafts to create")

    async def build_listing_sku_index(
        self, session, connection: PlatformConnection, *, enrich: bool = True, enrich_skus: set[str] | None = None
    ) -> dict[str, ExternalListingRef]:
        raise NotImplementedError("Square has no listings to index")

    # ---- Orders ----

    async def fetch_orders_since(
        self, session, connection: PlatformConnection, since: datetime | None
    ) -> list[ExternalOrder]:
        """SearchOrders filtered by updated_at — same reasoning as Etsy/eBay's
        fetch_orders_since: a status change on an already-imported order (a cancellation,
        a pickup being completed) must re-surface it, which a created_at filter never
        would once the watermark has advanced past it."""
        if connection.external_account_id is None:
            raise PlatformSyncError(
                "Square connection has no location selected — see routers/platforms.set_square_location"
            )

        updated_since = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if since else None

        orders: list[ExternalOrder] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            body = await square_client.search_orders(
                connection.access_token,
                connection.environment,
                connection.external_account_id,
                updated_since=updated_since,
                cursor=cursor,
                limit=_PAGE_LIMIT,
            )
            raw_orders = body.get("orders", [])
            for raw in raw_orders:
                orders.append(await self._parse_order(connection, raw))
            cursor = body.get("cursor")
            if not cursor or not raw_orders:
                break
        return orders

    async def _parse_order(self, connection: PlatformConnection, order: dict) -> ExternalOrder:
        placed_at = _parse_timestamp(order.get("created_at")) or datetime.now(timezone.utc)
        last_modified = _parse_timestamp(order.get("updated_at")) or placed_at
        is_cancelled = order.get("state") == "CANCELED"
        fulfillments = order.get("fulfillments") or []
        fulfilment_method, collect_by = _parse_fulfilment(fulfillments)
        # A fulfilment reaching COMPLETED covers both "collected" and "shipped" — StockSmith
        # has one terminal "shipped" status for both today (OrderStatus.shipped). Not
        # exercised by the sandbox spike, which never drove a fulfilment past PROPOSED.
        is_shipped = any(f.get("state") == "COMPLETED" for f in fulfillments)
        payment_state = _payment_state(order, is_cancelled)

        # Same enrich gate as Etsy/eBay: skip the extra GetPayment call for an order that's
        # unsettled (nothing to fetch yet) or wasn't touched by this sync window (already
        # has whatever financials an earlier sync recorded) — see ExternalOrder.financials_enriched.
        cutoff = ensure_utc(connection.last_orders_synced_at)
        enrich = payment_state is not PaymentState.unsettled and (cutoff is None or last_modified >= cutoff)
        payment_fees = payment_net = None
        if enrich:
            payment_fees, payment_net = await self._fetch_processing_fee(connection, order)

        lines = await self._parse_lines(connection, order.get("line_items") or [])

        return ExternalOrder(
            external_order_id=order["id"],
            buyer_name=None,
            buyer_note=None,
            placed_at=placed_at,
            last_modified=last_modified,
            is_cancelled=is_cancelled,
            is_shipped=is_shipped,
            fulfilment_method=fulfilment_method,
            collect_by=collect_by,
            lines=lines,
            raw=order,
            currency=(order.get("total_money") or {}).get("currency"),
            grand_total=_parse_money(order.get("total_money")),
            # Square's order response has no explicit subtotal field (confirmed against
            # the sandbox spike's raw JSON) — deriving one from total_money and the other
            # totals would mean guessing the exact formula without a real order that
            # actually has tax/discount to check it against. Left unset rather than guess;
            # revisit once a real order with both exists to verify against.
            subtotal=None,
            tax_charged=_parse_money(order.get("total_tax_money")),
            discount_amount=_parse_money(order.get("total_discount_money")),
            payment_fees=payment_fees,
            payment_net=payment_net,
            payment_state=payment_state,
            financials_enriched=enrich,
        )

    async def _parse_lines(self, connection: PlatformConnection, line_items: list[dict]) -> list[ExternalOrderLine]:
        product_lines = [li for li in line_items if (li.get("name") or "").strip().lower() != _DELIVERY_LINE_NAME]
        catalog_ids = [li["catalog_object_id"] for li in product_lines if li.get("catalog_object_id")]
        sku_by_catalog_id = await self._resolve_skus(connection, catalog_ids)
        return [
            ExternalOrderLine(
                external_line_id=li.get("uid", ""),
                sku=sku_by_catalog_id.get(li.get("catalog_object_id")),
                qty=int(li.get("quantity", "1")),
                unit_price=_parse_money(li.get("base_price_money")),
                currency=(li.get("base_price_money") or {}).get("currency"),
                variation_text=li.get("note"),
            )
            for li in product_lines
        ]

    async def _resolve_skus(self, connection: PlatformConnection, catalog_object_ids: list[str]) -> dict[str, str]:
        """catalog_object_id -> sku, via the Catalog API — a line item never carries its
        own sku directly. NOT yet exercised against a real sandbox catalogue item (see
        square_client.batch_retrieve_catalog_objects); verify before relying on this."""
        if not catalog_object_ids:
            return {}
        objects = await square_client.batch_retrieve_catalog_objects(
            connection.access_token, connection.environment, catalog_object_ids
        )
        return {
            obj["id"]: sku
            for obj in objects
            if (sku := (obj.get("item_variation_data") or {}).get("sku"))
        }

    async def _fetch_processing_fee(self, connection: PlatformConnection, order: dict) -> tuple[str | None, str | None]:
        """The follow-up GetPayment call the sandbox spike found necessary — processing_fee
        isn't on the payment Square returns at the moment it's taken."""
        tenders = order.get("tenders") or []
        if not tenders:
            return None, None
        payment_id = tenders[0].get("payment_id") or tenders[0].get("id")
        if not payment_id:
            return None, None
        payment = await square_client.get_payment(connection.access_token, connection.environment, payment_id)
        fee_entries = payment.get("processing_fee") or []
        if not fee_entries:
            return None, None
        fee_total = sum(entry.get("amount_money", {}).get("amount", 0) for entry in fee_entries)
        payment_fees = _parse_money({"amount": fee_total})
        total_amount = (order.get("total_money") or {}).get("amount")
        payment_net = _parse_money({"amount": total_amount - fee_total}) if total_amount is not None else None
        return payment_fees, payment_net
