import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.services.platforms.base import ExternalListingRef, ExternalOrder, ExternalOrderLine, TokenSet, ensure_utc
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

logger = logging.getLogger("stocksmith.ebay")

# Sandbox and Production are entirely separate keysets AND separate API hosts — see
# docs/plan-marketplace-integrations.md Section 2. auth./api. sandbox hosts are
# well-documented by eBay; apiz.sandbox.ebay.com follows the same apiz<->api naming
# eBay uses for its production identity host but, like everything else in this file
# per the class docstring below, hasn't been verified against a live Sandbox call.
_HOSTS: dict[PlatformEnvironment, dict[str, str]] = {
    PlatformEnvironment.production: {
        "authorize": "https://auth.ebay.com/oauth2/authorize",
        "token": "https://api.ebay.com/identity/v1/oauth2/token",
        "api": "https://api.ebay.com",
        "identity": "https://apiz.ebay.com",
    },
    PlatformEnvironment.sandbox: {
        "authorize": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api": "https://api.sandbox.ebay.com",
        "identity": "https://apiz.sandbox.ebay.com",
    },
}

_REFRESH_SKEW = timedelta(minutes=5)

# Same rationale as EtsyAdapter's own caps — bounds one sync/index-build click to a
# reasonable slice of the daily API budget rather than an unbounded crawl.
_MAX_PAGES = 20
_PAGE_LIMIT = 200  # eBay's documented max page size for getOrders
_MAX_RATE_LIMIT_RETRIES = 3


class EbayAdapter:
    """eBay Sell API adapter — standard OAuth 2.0 authorization-code grant (NOT PKCE;
    build_authorize_url/exchange_code accept and ignore code_challenge/code_verifier
    purely to satisfy the shared PlatformAdapter Protocol uniformly across adapters).
    Unlike Etsy, eBay's refresh token does not rotate on use and is long-lived
    (~18 months) — refresh() only ever returns a new access token.

    fetch_orders_since and build_listing_sku_index have been verified against a live
    Sandbox connection (empty-result parsing only — the test account had no orders or
    listings yet); push_listing_quantity has not, since no listing existed to push
    against. Treat any request/response shape not exercised that way as best-effort,
    same caveat every uncertain spot in this file already carries. Requires the
    commerce.identity.readonly scope alongside the Sell-API ones (see
    routers/platforms._SCOPES) — fetch_account_id 403s without it, confirmed live.
    """

    def __init__(
        self, client_id: str, client_secret: str, environment: PlatformEnvironment = PlatformEnvironment.production
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        hosts = _HOSTS[environment]
        self.authorize_url = hosts["authorize"]
        self.token_url = hosts["token"]
        self.api_base = hosts["api"]
        self.identity_base = hosts["identity"]

    @property
    def _basic_auth_header(self) -> str:
        token = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("ascii")).decode("ascii")
        return f"Basic {token}"

    def build_authorize_url(self, state: str, code_challenge: str, redirect_uri: str, scopes: list[str]) -> str:
        # Despite the Protocol's parameter name, eBay's `redirect_uri` is the RuName
        # identifier assigned to a redirect configuration in the dev portal, not a
        # literal URL — the caller (routers/platforms.py._redirect_uri) already knows
        # this and passes the RuName string through for this platform.
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
        }
        query = httpx.QueryParams(params)
        return f"{self.authorize_url}?{query}"

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self.token_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": self._basic_auth_header,
                },
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            )
        return self._parse_token_response(response, refresh_token_fallback=None)

    async def refresh(self, refresh_token: str) -> TokenSet:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self.token_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": self._basic_auth_header,
                },
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
        # eBay's refresh response does not include a new refresh_token — the same one
        # keeps working until its own ~18-month expiry, unlike Etsy's rotate-on-use.
        return self._parse_token_response(response, refresh_token_fallback=refresh_token)

    def _parse_token_response(self, response: httpx.Response, refresh_token_fallback: str | None) -> TokenSet:
        if response.status_code != 200:
            raise PlatformAuthError(f"eBay token endpoint returned {response.status_code}: {response.text}")
        body = response.json()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"])
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or refresh_token_fallback,
            expires_at=expires_at,
            scopes=body.get("scope"),
        )

    async def fetch_account_id(self, access_token: str) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.identity_base}/commerce/identity/v1/user/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            raise PlatformSyncError(f"Failed to resolve eBay account id: {response.status_code} {response.text}")
        body = response.json()
        account_id = body.get("username") or body.get("userId")
        if account_id is None:
            raise PlatformSyncError("eBay user response did not include a username or userId")
        return str(account_id)

    async def _authed_request(
        self, session, connection: PlatformConnection, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """Mirrors EtsyAdapter._authed_request — proactive refresh near expiry, reactive
        refresh once on a 401, then the same 429 backoff/retry loop."""
        await self._ensure_fresh(session, connection)

        response = await self._request_once(connection, method, url, **kwargs)
        if response.status_code == 401:
            await self._do_refresh(session, connection)
            response = await self._request_once(connection, method, url, **kwargs)

        attempt = 0
        while response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
            delay = self._rate_limit_delay(response, attempt)
            logger.warning("eBay API rate limited on %s %s (retry %d/%d in %.1fs)", method, url, attempt + 1, _MAX_RATE_LIMIT_RETRIES, delay)
            await asyncio.sleep(delay)
            response = await self._request_once(connection, method, url, **kwargs)
            attempt += 1

        if response.status_code == 429:
            raise PlatformRateLimitError("eBay API rate limit exceeded")
        return response

    @staticmethod
    def _rate_limit_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return float(2**attempt)

    async def _request_once(self, connection: PlatformConnection, method: str, url: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers = {**headers, "Authorization": f"Bearer {connection.access_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def _ensure_fresh(self, session, connection: PlatformConnection) -> None:
        expires_at = ensure_utc(connection.access_token_expires_at)
        if expires_at is None or connection.refresh_token is None:
            raise PlatformAuthError("eBay connection has no stored tokens — reconnect required")
        if datetime.now(timezone.utc) + _REFRESH_SKEW >= expires_at:
            await self._do_refresh(session, connection)

    async def _do_refresh(self, session, connection: PlatformConnection) -> None:
        if connection.refresh_token is None:
            raise PlatformAuthError("eBay connection has no refresh token — reconnect required")
        tokens = await self.refresh(connection.refresh_token)
        connection.access_token = tokens.access_token
        connection.refresh_token = tokens.refresh_token
        connection.access_token_expires_at = tokens.expires_at
        connection.last_refreshed_at = datetime.now(timezone.utc)
        await session.commit()

    async def fetch_orders_since(
        self, session, connection: PlatformConnection, since: datetime | None
    ) -> list[ExternalOrder]:
        """Sell Fulfillment API getOrders, filtered by lastmodifieddate (not
        creationdate) for the same reason as Etsy's fetch_orders_since: a status change
        on an already-imported order (shipped/cancelled) must re-surface it, which a
        creation-date filter could never do once the watermark has advanced past it."""
        params: dict[str, str | int] = {"limit": _PAGE_LIMIT, "offset": 0}
        if since is not None:
            params["filter"] = f"lastmodifieddate:[{since.strftime('%Y-%m-%dT%H:%M:%S.000Z')}..]"

        orders: list[ExternalOrder] = []
        for _ in range(_MAX_PAGES):
            response = await self._authed_request(
                session, connection, "GET", f"{self.api_base}/sell/fulfillment/v1/order", params=params
            )
            if response.status_code != 200:
                raise PlatformSyncError(f"Failed to fetch eBay orders: {response.status_code} {response.text}")

            body = response.json()
            results = body.get("orders", [])
            for order in results:
                orders.append(await self._parse_order(session, connection, order))

            total = body.get("total", len(results))
            params["offset"] = int(params["offset"]) + len(results)
            if not results or int(params["offset"]) >= total:
                break

        return orders

    async def _parse_order(self, session, connection: PlatformConnection, order: dict) -> ExternalOrder:
        buyer = order.get("buyer") or {}
        buyer_name = buyer.get("username")
        placed_at_raw = order.get("creationDate")
        placed_at = self._parse_timestamp(placed_at_raw) or datetime.now(timezone.utc)
        last_modified = self._parse_timestamp(order.get("lastModifiedDate")) or placed_at

        status = str(order.get("orderPaymentStatus", "")).upper()
        is_cancelled = status == "FAILED" or str(order.get("cancelStatus", {}).get("cancelState", "")).upper() == "CANCELED"
        fulfillment_status = str(order.get("orderFulfillmentStatus", "")).upper()
        is_shipped = fulfillment_status == "FULFILLED"

        line_items = order.get("lineItems", [])
        lines = [self._parse_line_item(li) for li in line_items]

        pricing = order.get("pricingSummary") or {}
        currency = (pricing.get("total") or {}).get("currency")

        payment_fees, payment_net, payment_status = await self._fetch_transactions(
            session, connection, order.get("orderId")
        )

        return ExternalOrder(
            external_order_id=str(order.get("orderId")),
            buyer_name=buyer_name,
            buyer_note=order.get("buyerCheckoutNotes"),
            placed_at=placed_at,
            last_modified=last_modified,
            is_cancelled=is_cancelled,
            is_shipped=is_shipped,
            lines=lines,
            raw=order,
            currency=currency,
            grand_total=self._parse_money(pricing.get("total")),
            subtotal=self._parse_money(pricing.get("priceSubtotal")),
            shipping_charged=self._parse_money(pricing.get("deliveryCost")),
            tax_charged=self._parse_money(pricing.get("tax")),
            discount_amount=self._parse_money(pricing.get("priceDiscountSubtotal")),
            payment_fees=payment_fees,
            payment_net=payment_net,
            payment_status=payment_status,
        )

    def _parse_line_item(self, line_item: dict) -> ExternalOrderLine:
        price = (line_item.get("lineItemCost") or {})
        return ExternalOrderLine(
            external_line_id=str(line_item.get("lineItemId")),
            sku=line_item.get("sku") or None,
            qty=int(line_item.get("quantity", 1)),
            unit_price=self._parse_money(price),
            currency=price.get("currency"),
        )

    @staticmethod
    def _parse_timestamp(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _parse_money(money: dict | None) -> str | None:
        if not money:
            return None
        value = money.get("value")
        return f"{float(value):.2f}" if value is not None else None

    async def _fetch_transactions(
        self, session, connection: PlatformConnection, order_id
    ) -> tuple[str | None, str | None, str | None]:
        """Sell Finances API getTransactions filtered by orderId — mirrors Etsy's
        per-receipt _fetch_payment. Sums SALE-type gross/fee amounts for this order;
        a transaction whose payout hasn't settled yet just means these stay None,
        matching Etsy's own "not failing the sync" behavior."""
        if order_id is None:
            return None, None, None
        response = await self._authed_request(
            session,
            connection,
            "GET",
            f"{self.api_base}/sell/finances/v1/transaction",
            params={"filter": f"orderId:{{{order_id}}}"},
        )
        if response.status_code != 200:
            return None, None, None
        results = response.json().get("transactions", [])
        sale = next((t for t in results if str(t.get("transactionType")).upper() == "SALE"), None)
        if sale is None:
            return None, None, None
        gross = (sale.get("amount") or {}).get("value")
        total_fees = sum(
            float((fee.get("amount") or {}).get("value", 0))
            for fee in sale.get("totalFeeBasisAmount", [])
            if isinstance(fee, dict)
        )
        # totalFeeBasisAmount's exact shape is uncertain without a live account to
        # verify against — falls back to totalFeeAmount if present, matching the
        # dedicated field described in eBay's Transaction schema.
        if not total_fees and sale.get("totalFeeAmount"):
            total_fees = float(sale["totalFeeAmount"].get("value", 0))
        net = float(gross) - total_fees if gross is not None else None
        return (
            f"{total_fees:.2f}" if total_fees else None,
            f"{net:.2f}" if net is not None else None,
            sale.get("transactionStatus"),
        )

    async def push_listing_quantity(
        self, session, connection: PlatformConnection, listing_ref: ExternalListingRef, sku: str | None, qty: int
    ) -> None:
        """Sell Inventory API bulkUpdatePriceQuantity, updating only this SKU's
        shipToLocationAvailability.quantity (no price/offer changes) — unlike Etsy's
        updateListingInventory, this is a targeted partial update, not a full replace, so
        there's no GET-then-PUT round trip needed.

        Unverified against a live listing — no listing existed on the connected Sandbox
        test account while building this (same caveat every other uncertain spot in this
        file already carries; see the class docstring). In particular, the per-SKU
        `responses[].statusCode`/`errors` shape below is inferred from eBay's docs, not
        confirmed against a real bulk response body.
        """
        if sku is None:
            raise PlatformSyncError("Cannot push a quantity update to eBay without a SKU")

        body = {"requests": [{"sku": sku, "shipToLocationAvailability": {"quantity": qty}}]}
        response = await self._authed_request(
            session, connection, "POST", f"{self.api_base}/sell/inventory/v1/bulk_update_price_quantity", json=body
        )
        if response.status_code != 200:
            raise PlatformSyncError(f"Failed to update eBay inventory quantity: {response.status_code} {response.text}")

        result = response.json()
        matched = next((r for r in result.get("responses", []) if r.get("sku") == sku), None)
        if matched is None:
            raise PlatformSyncError(f"eBay bulk_update_price_quantity response did not include SKU '{sku}'")
        status_code = matched.get("statusCode")
        if status_code is not None and not (200 <= status_code < 300):
            raise PlatformSyncError(
                f"eBay rejected the quantity update for SKU '{sku}': {status_code} {matched.get('errors')}"
            )

    async def build_listing_sku_index(
        self, session, connection: PlatformConnection
    ) -> dict[str, ExternalListingRef]:
        """Sell Inventory API getInventoryItems, paginated — unlike Etsy, eBay's
        Inventory API is SKU-keyed natively (a per-SKU getOffers/{sku} lookup exists
        too), but this builds the same bulk dict-of-SKU shape as EtsyAdapter's version
        so the shared listing_sync service and its UI need no special-casing. A
        per-SKU lookup path is a possible future optimization, not required today."""
        params: dict[str, str | int] = {"limit": _PAGE_LIMIT, "offset": 0}
        index: dict[str, ExternalListingRef] = {}

        for _ in range(_MAX_PAGES):
            response = await self._authed_request(
                session, connection, "GET", f"{self.api_base}/sell/inventory/v1/inventory_item", params=params
            )
            if response.status_code != 200:
                raise PlatformSyncError(f"Failed to fetch eBay inventory items: {response.status_code} {response.text}")

            body = response.json()
            results = body.get("inventoryItems", [])
            for item in results:
                self._index_inventory_item(item, index)

            total = body.get("total", len(results))
            params["offset"] = int(params["offset"]) + len(results)
            if not results or int(params["offset"]) >= total:
                break

        return index

    @staticmethod
    def _index_inventory_item(item: dict, index: dict[str, ExternalListingRef]) -> None:
        sku = item.get("sku")
        if not sku:
            return
        product = item.get("product") or {}
        availability = (item.get("availability") or {}).get("shipToLocationAvailability") or {}
        # eBay's inventory item alone doesn't carry a listing_id/state the way an Etsy
        # listing does — those live on the associated Offer (a separate per-SKU call).
        # Without a live account to confirm whether a bulk offer-listing endpoint
        # exists, this treats "has an inventory item at all" as "active" — a coarser
        # signal than Etsy's real listing state, to revisit once verified.
        index[sku] = ExternalListingRef(
            external_listing_id=sku,
            title=product.get("title") or "",
            sku=sku,
            state="active",
            quantity=int(availability.get("quantity", 0)),
            variation=None,
        )
