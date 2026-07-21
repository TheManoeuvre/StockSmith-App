import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx

from app.models.platform_connection import PlatformConnection
from app.services.platforms.base import ExternalListingRef, ExternalOrder, ExternalOrderLine, TokenSet
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

logger = logging.getLogger("stocksmith.etsy")

AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
API_BASE = "https://openapi.etsy.com/v3/application"

# Refresh this far ahead of actual expiry so a request never races a token that expires
# mid-flight.
_REFRESH_SKEW = timedelta(minutes=5)

# Safety cap on pagination per fetch_orders_since call — bounds how much of the daily
# rate-limit budget one sync attempt can spend (100/page * 20 = up to 2000 receipts).
_MAX_PAGES = 20

# Same idea for build_listing_sku_index — bounds a single "check sync" click to at most
# a few thousand listings' worth of API calls.
_MAX_LISTING_PAGES = 20

# Retries for a 429 before giving up and surfacing PlatformRateLimitError to the caller.
# Etsy's QPS window is one second, so a couple of short backoffs is usually enough to
# clear a transient limit hit without turning a single sync click into a long stall.
_MAX_RATE_LIMIT_RETRIES = 3


class EtsyAdapter:
    """Etsy Open API v3 adapter — OAuth 2.0 + PKCE, refresh-token rotation (Etsy always
    rotates the refresh token on use, so every refresh() result must be persisted).

    push_listing_quantity is intentionally unimplemented here — quantity push-back to
    Etsy is a deliberately deferred phase (see Stage 3 of the Etsy rollout plan: sync
    stays manual/pull-only for now). build_listing_sku_index is implemented (Stage 1).
    """

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._refresh_lock = asyncio.Lock()

    @property
    def _api_key(self) -> str:
        # Etsy requires keystring:shared_secret in x-api-key on every request, not just
        # the keystring — sending the keystring alone returns a 403 "Shared secret is
        # required in x-api-key header."
        return f"{self.client_id}:{self.client_secret}"

    def build_authorize_url(self, state: str, code_challenge: str, redirect_uri: str, scopes: list[str]) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        query = httpx.QueryParams(params)
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "redirect_uri": redirect_uri,
                    "code": code,
                    "code_verifier": code_verifier,
                },
            )
        return self._parse_token_response(response)

    async def refresh(self, refresh_token: str) -> TokenSet:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "refresh_token": refresh_token,
                },
            )
        return self._parse_token_response(response)

    def _parse_token_response(self, response: httpx.Response) -> TokenSet:
        if response.status_code != 200:
            raise PlatformAuthError(f"Etsy token endpoint returned {response.status_code}: {response.text}")
        body = response.json()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"])
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=expires_at,
            scopes=body.get("scope"),
        )

    async def fetch_account_id(self, access_token: str) -> str:
        # Etsy access tokens are formatted "<user_id>.<opaque>" — the user id is the
        # shop owner's user id, which the shops-by-user-id endpoint resolves to a shop.
        user_id = access_token.split(".", 1)[0]
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{API_BASE}/users/{user_id}/shops",
                headers={"Authorization": f"Bearer {access_token}", "x-api-key": self._api_key},
            )
        if response.status_code != 200:
            raise PlatformSyncError(f"Failed to resolve Etsy shop id: {response.status_code} {response.text}")
        body = response.json()

        shops = self._extract_shops(body)
        if len(shops) == 0:
            raise PlatformSyncError("This Etsy account has no shops")
        if len(shops) > 1:
            # Deliberately refuses to guess. Silently picking one of several shops is
            # exactly the failure mode that matters here — better to fail loudly than
            # risk connecting to the wrong (possibly live) store.
            names = ", ".join(str(s.get("shop_name") or s.get("shop_id")) for s in shops)
            raise PlatformSyncError(
                f"This Etsy account has multiple shops ({names}) — StockSmith can't safely pick one "
                "automatically. Connecting multi-shop accounts isn't supported yet."
            )
        shop_id = shops[0].get("shop_id")
        if shop_id is None:
            raise PlatformSyncError("Etsy shops response did not include a shop_id")
        return str(shop_id)

    @staticmethod
    def _extract_shops(body) -> list[dict]:
        """Etsy's shops-by-user response shape wasn't fully verifiable against live docs
        while this was built — handles a bare shop object, a bare array, or the
        paginated {count, results} shape used elsewhere in the API, rather than assuming
        one specific shape and silently mis-parsing whichever one Etsy actually returns."""
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            if "results" in body:
                return body["results"]
            if "shop_id" in body:
                return [body]
        return []

    async def _authed_request(
        self, session, connection: PlatformConnection, method: str, path: str, **kwargs
    ) -> httpx.Response:
        """Issues a request with the connection's access token, proactively refreshing
        if it's near expiry and reactively refreshing once on a 401. Any rotated tokens
        are persisted back to `connection` and committed before the caller sees a result."""
        await self._ensure_fresh(session, connection)

        response = await self._request_once(connection, method, path, **kwargs)
        if response.status_code == 401:
            await self._do_refresh(session, connection)
            response = await self._request_once(connection, method, path, **kwargs)

        attempt = 0
        while response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
            delay = self._rate_limit_delay(response, attempt)
            logger.warning(
                "Etsy API rate limited on %s %s (retry %d/%d in %.1fs)",
                method,
                path,
                attempt + 1,
                _MAX_RATE_LIMIT_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)
            response = await self._request_once(connection, method, path, **kwargs)
            attempt += 1

        if response.status_code == 429:
            raise PlatformRateLimitError("Etsy API rate limit exceeded")
        return response

    @staticmethod
    def _rate_limit_delay(response: httpx.Response, attempt: int) -> float:
        """Prefers Etsy's `retry-after` header; falls back to exponential backoff with
        jitter (1s, 2s, 4s, ...) if the header is absent or unparsable."""
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return (2**attempt) + random.uniform(0, 0.5)

    async def _request_once(self, connection: PlatformConnection, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers = {**headers, "Authorization": f"Bearer {connection.access_token}", "x-api-key": self._api_key}
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)

    async def _ensure_fresh(self, session, connection: PlatformConnection) -> None:
        if connection.access_token_expires_at is None or connection.refresh_token is None:
            raise PlatformAuthError("Etsy connection has no stored tokens — reconnect required")
        if datetime.now(timezone.utc) + _REFRESH_SKEW >= connection.access_token_expires_at:
            await self._do_refresh(session, connection)

    async def _do_refresh(self, session, connection: PlatformConnection) -> None:
        async with self._refresh_lock:
            # Another task may have already refreshed while we waited for the lock.
            if (
                connection.access_token_expires_at is not None
                and datetime.now(timezone.utc) + _REFRESH_SKEW < connection.access_token_expires_at
            ):
                return
            if connection.refresh_token is None:
                raise PlatformAuthError("Etsy connection has no refresh token — reconnect required")
            tokens = await self.refresh(connection.refresh_token)
            connection.access_token = tokens.access_token
            connection.refresh_token = tokens.refresh_token
            connection.access_token_expires_at = tokens.expires_at
            connection.last_refreshed_at = datetime.now(timezone.utc)
            await session.commit()

    async def fetch_orders_since(
        self, session, connection: PlatformConnection, since: datetime | None
    ) -> list[ExternalOrder]:
        """Fetches receipts (orders) created OR updated since `since` — using Etsy's
        min_last_modified filter rather than min_created is what lets an already-imported
        order's status change (shipped, cancelled) surface on a later sync. Using
        min_created instead would mean an order is only ever fetched once, in the sync
        immediately after it's placed, since the watermark only advances forward and an
        old order's create_timestamp can never catch back up to it — silently making
        order_sync._reconcile_status's "already-imported" branch unreachable.

        Newest fields are defensively parsed since Etsy's exact response schema wasn't
        verifiable against live docs while building this — every ExternalOrder carries
        its untouched `raw` receipt dict alongside the parsed fields specifically so a
        human can sanity-check the parsing against ground truth before this is ever
        trusted to run unattended.

        Capped at _MAX_PAGES pages per call (a few thousand receipts) as a safety limit
        on how much of the daily rate-limit budget one sync attempt can spend.
        """
        if connection.external_account_id is None:
            raise PlatformSyncError("Etsy connection has no shop id — reconnect required")

        params: dict[str, str | int] = {"limit": 100, "offset": 0, "includes": "Transactions"}
        if since is not None:
            params["min_last_modified"] = int(since.timestamp())

        orders: list[ExternalOrder] = []
        for _ in range(_MAX_PAGES):
            response = await self._authed_request(
                session, connection, "GET", f"/shops/{connection.external_account_id}/receipts", params=params
            )
            if response.status_code != 200:
                raise PlatformSyncError(f"Failed to fetch Etsy receipts: {response.status_code} {response.text}")

            body = response.json()
            results = body.get("results", [])
            for receipt in results:
                orders.append(await self._parse_receipt(session, connection, receipt))

            total = body.get("count", len(results))
            params["offset"] = int(params["offset"]) + len(results)
            if not results or int(params["offset"]) >= total:
                break

        return orders

    async def _parse_receipt(self, session, connection: PlatformConnection, receipt: dict) -> ExternalOrder:
        buyer_name = receipt.get("name") or receipt.get("first_line") or None
        placed_ts = receipt.get("create_timestamp") or receipt.get("created_timestamp")
        placed_at = (
            datetime.fromtimestamp(placed_ts, tz=timezone.utc) if placed_ts is not None else datetime.now(timezone.utc)
        )
        status = str(receipt.get("status", "")).lower()
        is_cancelled = status in ("canceled", "cancelled") or bool(receipt.get("is_canceled"))
        is_shipped = bool(receipt.get("is_shipped", False))

        transactions = receipt.get("transactions")
        if transactions is None:
            # The `includes=Transactions` embed didn't come through — fall back to a
            # dedicated per-receipt request rather than silently returning an order
            # with no lines.
            tx_response = await self._authed_request(
                session,
                connection,
                "GET",
                f"/shops/{connection.external_account_id}/receipts/{receipt.get('receipt_id')}/transactions",
            )
            transactions = tx_response.json().get("results", []) if tx_response.status_code == 200 else []

        lines = [self._parse_transaction(tx) for tx in transactions]

        grand_total = self._parse_money(receipt.get("grandtotal"))
        currency = (receipt.get("grandtotal") or {}).get("currency_code")

        payment_fees, payment_net, payment_status = await self._fetch_payment(
            session, connection, receipt.get("receipt_id")
        )

        return ExternalOrder(
            external_order_id=str(receipt.get("receipt_id")),
            buyer_name=buyer_name,
            buyer_note=receipt.get("message_from_buyer"),
            placed_at=placed_at,
            is_cancelled=is_cancelled,
            is_shipped=is_shipped,
            lines=lines,
            raw=receipt,
            currency=currency,
            grand_total=grand_total,
            subtotal=self._parse_money(receipt.get("subtotal")),
            shipping_charged=self._parse_money(receipt.get("total_shipping_cost")),
            tax_charged=self._parse_money(receipt.get("total_tax_cost")),
            vat_charged=self._parse_money(receipt.get("total_vat_cost")),
            discount_amount=self._parse_money(receipt.get("discount_amt")),
            refunded_amount=self._sum_refunds(receipt.get("refunds")),
            payment_fees=payment_fees,
            payment_net=payment_net,
            payment_status=payment_status,
        )

    def _parse_transaction(self, tx: dict) -> ExternalOrderLine:
        price = tx.get("price") or {}
        amount = price.get("amount")
        divisor = price.get("divisor") or 1
        unit_price = f"{amount / divisor:.2f}" if amount is not None else None
        return ExternalOrderLine(
            external_line_id=str(tx.get("transaction_id")),
            sku=tx.get("sku") or None,
            qty=int(tx.get("quantity", 1)),
            unit_price=unit_price,
            currency=price.get("currency_code"),
        )

    @staticmethod
    def _parse_money(money: dict | None) -> str | None:
        if not money:
            return None
        amount = money.get("amount")
        divisor = money.get("divisor") or 1
        return f"{amount / divisor:.2f}" if amount is not None else None

    @classmethod
    def _sum_refunds(cls, refunds: list[dict] | None) -> str | None:
        if not refunds:
            return None
        total = 0.0
        found = False
        for refund in refunds:
            amount = (refund or {}).get("amount") or {}
            value = cls._parse_money(amount)
            if value is not None:
                total += float(value)
                found = True
        return f"{total:.2f}" if found else None

    async def _fetch_payment(
        self, session, connection: PlatformConnection, receipt_id
    ) -> tuple[str | None, str | None, str | None]:
        """getShopPaymentByReceiptId — a separate call per receipt for Etsy's own
        gross/fees/net breakdown. An order whose payment hasn't settled yet (or any
        non-200 response) just means these stay None; it doesn't fail the sync."""
        if receipt_id is None:
            return None, None, None
        response = await self._authed_request(
            session, connection, "GET", f"/shops/{connection.external_account_id}/receipts/{receipt_id}/payments"
        )
        if response.status_code != 200:
            return None, None, None
        results = response.json().get("results", [])
        if not results:
            return None, None, None
        payment = results[0]
        return (
            self._parse_money(payment.get("amount_fees")),
            self._parse_money(payment.get("amount_net")),
            payment.get("status"),
        )

    async def push_listing_quantity(self, session, connection: PlatformConnection, listing_ref, sku, qty) -> None:
        raise NotImplementedError("Quantity push-back to Etsy is deliberately deferred — see rollout Stage 3")

    async def build_listing_sku_index(
        self, session, connection: PlatformConnection
    ) -> dict[str, ExternalListingRef]:
        """Etsy's API has no "find listing by SKU" endpoint — the only way to resolve a
        SKU is to page through the shop's entire listing catalog (all states, via
        includes=Inventory so each listing's per-product SKUs come back in the same
        call) and index every SKU found. Since this costs the same whether checking one
        product or the whole catalog, callers should build this once and reuse it across
        every product/variant being checked in a given "test sync" run.
        """
        if connection.external_account_id is None:
            raise PlatformSyncError("Etsy connection has no shop id — reconnect required")

        params: dict[str, str | int] = {"limit": 100, "offset": 0, "includes": "Inventory"}
        index: dict[str, ExternalListingRef] = {}

        for _ in range(_MAX_LISTING_PAGES):
            response = await self._authed_request(
                session, connection, "GET", f"/shops/{connection.external_account_id}/listings", params=params
            )
            if response.status_code != 200:
                raise PlatformSyncError(f"Failed to fetch Etsy listings: {response.status_code} {response.text}")

            body = response.json()
            results = body.get("results", [])
            for listing in results:
                self._index_listing_skus(listing, index)

            total = body.get("count", len(results))
            params["offset"] = int(params["offset"]) + len(results)
            if not results or int(params["offset"]) >= total:
                break

        return index

    @staticmethod
    def _index_listing_skus(listing: dict, index: dict[str, ExternalListingRef]) -> None:
        listing_id = str(listing.get("listing_id"))
        title = listing.get("title") or ""
        state = listing.get("state") or "unknown"
        inventory = listing.get("inventory") or {}
        for product in inventory.get("products", []):
            sku = product.get("sku")
            if not sku or product.get("is_deleted"):
                continue
            qty = sum(
                int(offering.get("quantity", 0))
                for offering in product.get("offerings", [])
                if offering.get("is_enabled") and not offering.get("is_deleted")
            )
            index[sku] = ExternalListingRef(
                external_listing_id=listing_id,
                title=title,
                sku=sku,
                state=state,
                quantity=qty,
                variation=EtsyAdapter._format_variation(product.get("property_values", [])),
            )

    @staticmethod
    def _format_variation(property_values: list[dict]) -> str | None:
        parts = [
            f"{pv.get('property_name')}: {', '.join(pv.get('values', []))}"
            for pv in property_values
            if pv.get("property_name") and pv.get("values")
        ]
        return ", ".join(parts) if parts else None
