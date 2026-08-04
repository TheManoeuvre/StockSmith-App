import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import httpx

from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.services.platforms.base import (
    ClassicListingCandidate,
    ExternalListingRef,
    ExternalOrder,
    ExternalOrderLine,
    MigrationResult,
    PaymentState,
    TokenSet,
    ensure_utc,
)
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

# eBay's Trading API is XML/SOAP-era and namespaces every element under this URI —
# every ElementTree find/findall against a Trading API response must qualify tags with
# this prefix map or they silently match nothing (ElementTree has no implicit default
# namespace handling).
_TRADING_NS = {"e": "urn:ebay:apis:eBLBaseComponents"}

logger = logging.getLogger("stocksmith.ebay")

# eBay's OrderPaymentStatusEnum, mapped onto StockSmith's payment states. The full enum
# is FAILED, FULLY_REFUNDED, PAID, PARTIALLY_REFUNDED, PENDING — all five are covered
# here so an unrecognised value is genuinely unrecognised rather than merely unhandled.
#
# PARTIALLY_REFUNDED counts as settled: it was paid, and only part came back. eBay's own
# docs say of PAID that "it is safe for the seller to ship the order to the buyer", which
# is exactly the property this gate needs.
_EBAY_PAYMENT_STATES: dict[str, PaymentState] = {
    "PAID": PaymentState.settled,
    "PARTIALLY_REFUNDED": PaymentState.settled,
    "FULLY_REFUNDED": PaymentState.reversed,
    "PENDING": PaymentState.unsettled,
    "FAILED": PaymentState.unsettled,
}


def _order_payment_state(order: dict) -> PaymentState:
    """Whether eBay has actually taken this buyer's money.

    eBay's checkout flow means most orders reaching getOrders are already PAID, but that
    is not guaranteed — some payment methods (COD among them) can surface a non-PAID
    status — so this checks the field explicitly rather than trusting the API to have
    pre-filtered. An unknown or missing value falls through to `unsettled`, so a future
    enum addition fails closed.

    Module-level and pure, matching etsy._receipt_payment_state, so the truth table is
    unit-testable without a session or network.
    """
    raw = str(order.get("orderPaymentStatus", "")).upper()
    return _EBAY_PAYMENT_STATES.get(raw, PaymentState.unsettled)


class EbayTimeout(PlatformSyncError):
    """A request to eBay that never came back.

    A PlatformSyncError subclass so every existing caller keeps handling it sensibly
    (mapped to a 502 rather than escaping as an opaque 500), but a distinct type so the
    two calls that actually change state on eBay's side — migrate_listing and
    revise_listing_skus — can catch it specifically and say the one thing that matters:
    a timeout is not proof that nothing happened."""


def _xml_escape(value: str) -> str:
    """Trading API request bodies are hand-built XML strings, so any value interpolated
    into one must be escaped. Item IDs and SKUs both reach these builders from user-
    controlled input (a URL path parameter and a StockSmith SKU field respectively), so
    this is a correctness *and* injection boundary, not just tidiness."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _raise_for_trading_ack(root: ElementTree.Element, call_name: str) -> None:
    """The Trading API signals failure in the body, not the HTTP status — a call that
    fails validation still comes back 200 with Ack=Failure. Checking only
    response.status_code would silently treat those as empty successes."""
    ack = root.findtext("e:Ack", namespaces=_TRADING_NS)
    if ack in ("Success", "Warning"):
        return
    messages = [
        error.findtext("e:LongMessage", "", _TRADING_NS) or error.findtext("e:ShortMessage", "", _TRADING_NS)
        for error in root.findall("e:Errors", _TRADING_NS)
    ]
    detail = "; ".join(m for m in messages if m) or f"Ack={ack}"
    raise PlatformSyncError(f"eBay {call_name} failed: {detail}")


# Substrings eBay uses when rejecting a bulkMigrateListing call for a listing that is
# already an Inventory API object. Matched loosely (case-insensitive substring) because
# the exact wording is unverified against a live account and a missed match here would
# turn a harmless retry into a hard failure — the cost of a false positive is only that
# a genuinely-failed migration gets its SKUs read back and found absent, which surfaces
# as a normal "not found" a moment later.
_ALREADY_MIGRATED_MARKERS = ("already migrated", "already exists", "already an inventory item")


def _align_new_skus(candidate: ClassicListingCandidate, new_skus: list[str]) -> list[str]:
    """Validates the SKU list handed to a ReviseFixedPriceItem build.

    Length mismatch on a multi-variation listing is the one mistake that could silently
    destroy variations (eBay deletes any variation omitted from a Variations block), so
    it fails loudly here rather than producing a partial payload. Empty SKUs are
    rejected for the same reason — eBay would accept the revision and leave a variation
    unidentifiable to the Inventory API afterwards."""
    if candidate.variation_specifics is None:
        if len(new_skus) != 1:
            raise PlatformSyncError(
                f"Listing {candidate.external_listing_id} has no variations — expected exactly 1 SKU, "
                f"got {len(new_skus)}"
            )
    elif len(new_skus) != len(candidate.variation_specifics):
        raise PlatformSyncError(
            f"Listing {candidate.external_listing_id} has {len(candidate.variation_specifics)} variation(s) but "
            f"{len(new_skus)} SKU(s) were supplied — every variation must be included or eBay would delete the "
            "omitted ones."
        )
    if any(not sku or not sku.strip() for sku in new_skus):
        raise PlatformSyncError(f"Listing {candidate.external_listing_id}: every variation needs a non-empty SKU")
    return [sku.strip() for sku in new_skus]


def _is_already_migrated_error(migration_response: dict) -> bool:
    """Whether a bulkMigrateListing per-item rejection means "nothing to do" rather than
    a real failure. Pure so the retry/idempotency contract is unit-testable without a
    live migration."""
    errors = migration_response.get("errors") or []
    for error in errors:
        if not isinstance(error, dict):
            continue
        text = f"{error.get('message', '')} {error.get('longMessage', '')}".lower()
        if any(marker in text for marker in _ALREADY_MIGRATED_MARKERS):
            return True
    return False


def _evaluate_eligibility(
    listing_type: str,
    skus: list[str],
    variation_specifics: list[dict[str, str]] | None,
    detail_loaded: bool = True,
) -> list[str]:
    """Best-effort local pre-filter for bulkMigrateListing eligibility — NOT
    authoritative. eBay's own migration call is the real gate (see
    EbayAdapter.migrate_listing); this only exists so the "unmigrated listings" report
    can show a human a reason before they waste a click on a listing eBay will reject
    anyway. Pure and dict/list-in, list-out on purpose so it's testable without a
    session or network, matching _order_payment_state's style above.

    `detail_loaded=False` means the caller only has the bulk-list view of this listing,
    which doesn't carry per-variation SKUs — SKU-based reasons are then suppressed
    entirely rather than guessed at. Getting this wrong in the safe direction matters:
    wrongly claiming "no SKU set" would grey out, in the picker, exactly the
    multi-variation listings this feature exists to adopt."""
    reasons: list[str] = []

    if listing_type in _INELIGIBLE_LISTING_TYPES:
        reasons.append(f"Listing type '{listing_type}' is not supported by eBay's Inventory API migration.")

    if not detail_loaded:
        return reasons

    non_empty_skus = [s for s in skus if s]
    if not non_empty_skus:
        reasons.append("Listing has no SKU set — add a Custom Label (SKU) in Seller Hub before migrating.")
    elif variation_specifics is not None:
        missing_variation_skus = sum(1 for sku in skus if not sku)
        if missing_variation_skus:
            reasons.append(
                f"{missing_variation_skus} of {len(skus)} variation(s) have no SKU — every variation needs one "
                "before the whole listing can migrate."
            )

    return reasons

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
        "trading": "https://api.ebay.com/ws/api.dll",
    },
    PlatformEnvironment.sandbox: {
        "authorize": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        "api": "https://api.sandbox.ebay.com",
        "identity": "https://apiz.sandbox.ebay.com",
        "trading": "https://api.sandbox.ebay.com/ws/api.dll",
    },
}

_REFRESH_SKEW = timedelta(minutes=5)

# Same rationale as EtsyAdapter's own caps — bounds one sync/index-build click to a
# reasonable slice of the daily API budget rather than an unbounded crawl.
_MAX_PAGES = 20
_PAGE_LIMIT = 200  # eBay's documented max page size for getOrders
# getInventoryItems' documented limit range is 1-100 (default 100) — distinct from
# getOrders' 200 above; conflating the two was a latent bug (never caught because it
# would 400 loudly rather than silently drop items, and no live account with >100
# inventory items had exercised this path until now).
_INVENTORY_PAGE_LIMIT = 100
_MAX_RATE_LIMIT_RETRIES = 3

# eBay answers a transient fault on its own side with a 5xx and errorId 25001
# ("A system error has occurred. Dependent service failure") — confirmed live on
# bulkMigrateListing. It is not a rejection of the request, so failing hard on the first
# one leaves the user to notice an alarming raw-JSON error and re-click the button
# themselves. Retried on a smaller budget than 429: a rate limit clears on a schedule and
# is worth waiting out, whereas a 5xx that survives two retries is more likely a real
# outage than a hiccup, and every retry here is spent inside a user-facing click.
#
# Safe for every call this adapter makes: the reads are idempotent by nature, and the two
# writes are idempotent by design — migrate_listing treats an "already migrated"
# rejection as success (see its docstring) and bulk_update_price_quantity sets an
# absolute quantity rather than adjusting by a delta.
_MAX_SERVER_ERROR_RETRIES = 2

# Concurrency for the getOffers fan-out in _enrich_with_offers.
#
# Deliberately NOT listing_push._MAX_CONCURRENT_PUSHES (3): that cap exists because each
# push opens its OWN database session and holds it across in-place retries, which
# exhausted the SQLAlchemy pool. These tasks share the caller's single session and do no
# database I/O in the fan-out, so the pool isn't the constraint here. What this bounds is
# eBay's short-window rate limit and the socket burst; the Inventory API's daily budget is
# not a factor. What it trades against is wall-clock latency on a user-facing click.
_MAX_CONCURRENT_OFFER_LOOKUPS = 8

# eBay's ListingStatusEnum, as it appears on offer.listing.listingStatus.
#
# OUT_OF_STOCK maps to "active" on purpose. It's a live GTC listing sitting at quantity 0
# — eBay's Out-of-Stock Control keeps such a listing alive (hidden from search) for up to
# 90 days — and it's frequently 0 because StockSmith itself pushed 0. Mapping it anywhere
# else would make listing_sync._status_from_match report listing_not_active for every
# legitimately sold-out item, i.e. flag this app's own correct behaviour as a sync
# failure. The quantity column already says 0.
_EBAY_LISTING_STATES = {
    "ACTIVE": "active",
    "OUT_OF_STOCK": "active",
    "INACTIVE": "inactive",
    "ENDED": "ended",
}

# Most eBay calls answer well inside this. Kept deliberately tight so a hung request
# surfaces quickly rather than blocking a user-facing action for a minute.
_DEFAULT_TIMEOUT = 15.0

# bulkMigrateListing and ReviseFixedPriceItem are the exceptions: both do real work on
# eBay's side proportional to the listing's variation count — migration creates an
# inventory item AND an offer per variation before responding. Confirmed live: a
# 4-variation listing blew straight through the 15s default and surfaced as an
# unhandled ReadTimeout, while a smaller listing had completed comfortably. These are
# also the two calls where a timeout is most consequential, since eBay may well have
# carried on and completed the work after we stopped listening.
_MIGRATION_TIMEOUT = 180.0
_REVISE_TIMEOUT = 90.0

# GetMyeBaySelling's ActiveList is a heavier call than the Sell REST APIs above, and the
# Trading API's default budget is 5,000 calls/DAY across every Trading call combined —
# roughly 400x tighter than the Inventory API's 2,000,000 (see
# docs/plan-ebay-existing-store-onboarding.md section 5). Short-duration limits are
# generous (300/15s for this call), so the daily total is the real constraint: every
# Trading call in this file must be user-initiated, never polled on a timer or fetched
# speculatively on page load.
_MAX_TRADING_PAGES = 20
_TRADING_PAGE_LIMIT = 200  # eBay's documented max EntriesPerPage for GetMyeBaySelling
_TRADING_COMPATIBILITY_LEVEL = "1413"

# The Trading API's X-EBAY-API-SITEID header selects which eBay site the call runs
# against, and a seller's listings only come back under their own site — hardcoding one
# would silently return an empty (or wrong) listing set for every seller registered
# elsewhere. Resolved per connection from GetUser's <Site> (see
# EbayAdapter._resolve_site_id) and mapped through this table.
_TRADING_SITE_IDS: dict[str, str] = {
    "US": "0",
    "Canada": "2",
    "UK": "3",
    "Australia": "15",
    "Austria": "16",
    "Belgium_French": "23",
    "France": "71",
    "Germany": "77",
    "Italy": "101",
    "Belgium_Dutch": "123",
    "Netherlands": "146",
    "Spain": "186",
    "Switzerland": "193",
    "Ireland": "205",
    "Poland": "212",
    "CanadaFrench": "210",
}
# Used only when GetUser can't be reached or returns a site name absent from the table
# above. 0 (US) is eBay's own default for the header, so an unknown site degrades to
# eBay's default behaviour rather than to a guess about this particular seller.
_DEFAULT_TRADING_SITE_ID = "0"

# Listing types eBay's Inventory API migration does not support, per eBay's own
# bulkMigrateListing documentation. This is a best-effort local pre-filter, not
# authoritative — bulkMigrateListing's own rejection is the real gate (see
# migrate_listing's docstring).
_INELIGIBLE_LISTING_TYPES = {"Chinese", "Classified"}


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

    The listing-adoption methods (fetch_classic_listings, fetch_classic_listing_detail,
    revise_listing_skus, migrate_listing, and _resolve_site_id) are ENTIRELY UNVERIFIED
    against a live account. They also introduce a second, wholly different API surface
    to this file: eBay's legacy Trading API, which is XML rather than JSON, reports
    failure in the response body rather than the HTTP status (hence
    _raise_for_trading_ack), and needs the base api_scope plus a per-seller site id.
    revise_listing_skus in particular writes to a live listing and can delete variations
    if its payload is wrong — see its docstring and test_sku_alignment.py. See
    docs/listing-adoption.md for the operator-facing version of all this.
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
        self.trading_base = hosts["trading"]
        # Resolved lazily on the first Trading API call and cached for this adapter's
        # lifetime — adapters are cached per (platform, environment) in the registry
        # (services/platforms/__init__.py), so this costs one extra GetUser call per
        # process, not per request. A seller's registration site doesn't change.
        self._site_id: str | None = None
        # Serialises token refresh. Every other eBay call in this file is sequential, but
        # _enrich_with_offers fans out concurrently on one AsyncSession — and _do_refresh
        # commits on that session, which is NOT concurrency-safe. Without this, a token
        # expiring mid-fan-out means several tasks refreshing at once: redundant refresh
        # POSTs, racing commits, and a last-writer-wins token.
        #
        # Safe to build here even though adapters are constructed outside a running loop
        # (the registry caches them): since 3.10 asyncio.Lock no longer binds an event
        # loop at construction time.
        self._refresh_lock = asyncio.Lock()

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
        refresh once on a 401, then a backoff/retry loop covering both 429 and 5xx.

        Network-level failures are translated into PlatformSyncError rather than being
        allowed to escape as raw httpx exceptions: every caller already handles
        PlatformError and maps it to a sensible status, whereas an httpx.ReadTimeout
        propagates all the way out as an opaque "Internal server error" (confirmed live
        — that is exactly how a slow bulkMigrateListing surfaced). Callers that need
        operation-specific wording catch httpx.TimeoutException themselves before this
        can reach them.

        A 5xx that outlives its retry budget is deliberately returned, not raised: every
        caller already fails on a non-200 with wording specific to the operation
        ("Failed to migrate eBay listing: 500 …"), which is more use to the user than a
        generic transport error raised from here."""
        await self._ensure_fresh(session, connection)

        try:
            response = await self._request_once(connection, method, url, **kwargs)
            if response.status_code == 401:
                await self._do_refresh(session, connection)
                response = await self._request_once(connection, method, url, **kwargs)

            # Separate budgets, so a call that hits a rate limit and then a server error
            # doesn't find one exhausted by the other — they're unrelated faults.
            rate_limit_attempts = 0
            server_error_attempts = 0
            while True:
                if response.status_code == 429 and rate_limit_attempts < _MAX_RATE_LIMIT_RETRIES:
                    delay = self._retry_delay(response, rate_limit_attempts)
                    logger.warning(
                        "eBay API rate limited on %s %s (retry %d/%d in %.1fs)",
                        method, url, rate_limit_attempts + 1, _MAX_RATE_LIMIT_RETRIES, delay,
                    )
                    rate_limit_attempts += 1
                elif response.status_code >= 500 and server_error_attempts < _MAX_SERVER_ERROR_RETRIES:
                    delay = self._retry_delay(response, server_error_attempts)
                    logger.warning(
                        "eBay API server error %d on %s %s (retry %d/%d in %.1fs)",
                        response.status_code, method, url, server_error_attempts + 1,
                        _MAX_SERVER_ERROR_RETRIES, delay,
                    )
                    server_error_attempts += 1
                else:
                    break
                await asyncio.sleep(delay)
                response = await self._request_once(connection, method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise EbayTimeout(f"eBay did not respond in time for {method} {url}") from e
        except httpx.HTTPError as e:
            raise PlatformSyncError(f"Could not reach eBay for {method} {url}: {e}") from e

        if response.status_code == 429:
            raise PlatformRateLimitError("eBay API rate limit exceeded")
        return response

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """Backoff for both the 429 and 5xx retry paths. eBay sends Retry-After on rate
        limits and generally not on server errors, so a 5xx falls through to the
        exponential 1s/2s/… below — which is the intent either way."""
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return float(2**attempt)

    async def _request_once(self, connection: PlatformConnection, method: str, url: str, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", _DEFAULT_TIMEOUT)
        headers = kwargs.pop("headers", {})
        headers = {**headers, "Authorization": f"Bearer {connection.access_token}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def _ensure_fresh(self, session, connection: PlatformConnection) -> None:
        expires_at = ensure_utc(connection.access_token_expires_at)
        if expires_at is None or connection.refresh_token is None:
            raise PlatformAuthError("eBay connection has no stored tokens — reconnect required")
        if datetime.now(timezone.utc) + _REFRESH_SKEW >= expires_at:
            await self._do_refresh(session, connection)

    async def _do_refresh(self, session, connection: PlatformConnection) -> None:
        """Refreshes the access token, at most once across concurrent callers.

        Deduplicated on the token's identity rather than its expiry, because this is
        called from two places that disagree about expiry: _ensure_fresh (the token is
        past its clock expiry) and the 401 handler (eBay rejected a token that still
        looks valid to us). Checking expiry under the lock would correctly skip the
        redundant proactive refresh but would also skip the reactive one, leaving the 401
        unrecoverable. "Did somebody else already replace the token I was unhappy with?"
        is the right question for both."""
        token_before = connection.access_token
        async with self._refresh_lock:
            if connection.access_token != token_before:
                return  # another task refreshed while we waited — that refresh is ours too
            await self._refresh_now(session, connection)

    async def _refresh_now(self, session, connection: PlatformConnection) -> None:
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

        # A cancellation is now read ONLY from cancelStatus. This previously also treated
        # orderPaymentStatus == FAILED as a cancellation, conflating two genuinely
        # different things: eBay had not been paid, versus the order was called off. A
        # failed payment now routes to PaymentState.unsettled instead, which is both more
        # accurate and strictly safer — an unpaid order is never imported at all, rather
        # than being imported and then flagged.
        is_cancelled = str(order.get("cancelStatus", {}).get("cancelState", "")).upper() == "CANCELED"
        fulfillment_status = str(order.get("orderFulfillmentStatus", "")).upper()
        is_shipped = fulfillment_status == "FULFILLED"

        line_items = order.get("lineItems", [])
        lines = [self._parse_line_item(li) for li in line_items]

        pricing = order.get("pricingSummary") or {}
        currency = (pricing.get("total") or {}).get("currency")

        payment_state = _order_payment_state(order)

        # See the equivalent block in the Etsy adapter's _parse_receipt for why
        # enrichment is skipped for unsettled orders and for orders older than the sync
        # watermark. Same reasoning, same no-op-when-no-hold-is-active property; here it
        # saves one Sell Finances API call per order, which is the N+1 in this adapter.
        cutoff = ensure_utc(connection.last_orders_synced_at)
        enrich = payment_state is not PaymentState.unsettled and (cutoff is None or last_modified >= cutoff)

        payment_fees = payment_net = payment_status = None
        if enrich:
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
            # deliveryCost is documented as "before any shipping/delivery discount is
            # applied" — confirmed live: showed the pre-discount amount as what the buyer
            # paid, overstating it by exactly the deliveryDiscount on an order that had
            # one. What the buyer actually paid is deliveryCost minus deliveryDiscount.
            shipping_charged=self._net_money(pricing.get("deliveryCost"), pricing.get("deliveryDiscount")),
            tax_charged=self._parse_money(pricing.get("tax")),
            discount_amount=self._parse_money(pricing.get("priceDiscountSubtotal")),
            payment_fees=payment_fees,
            payment_net=payment_net,
            payment_status=payment_status,
            payment_state=payment_state,
            financials_enriched=enrich,
        )

    def _parse_line_item(self, line_item: dict) -> ExternalOrderLine:
        # lineItemCost is the TOTAL for the line, not a per-unit price — confirmed
        # live: showed exactly double the correct value on a qty-1 line where the
        # buyer had actually been charged for 2 units bundled into one line item.
        cost = (line_item.get("lineItemCost") or {})
        qty = int(line_item.get("quantity", 1))
        total_value = cost.get("value")
        unit_price = f"{float(total_value) / qty:.2f}" if total_value is not None and qty > 0 else None
        return ExternalOrderLine(
            external_line_id=str(line_item.get("lineItemId")),
            sku=line_item.get("sku") or None,
            qty=qty,
            unit_price=unit_price,
            currency=cost.get("currency"),
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

    @staticmethod
    def _net_money(gross: dict | None, discount: dict | None) -> str | None:
        # eBay reports deliveryDiscount as an already-negative delta (e.g. "-3.4" against
        # a deliveryCost of "7.2") — confirmed live via the raw pricingSummary payload, so
        # this must ADD the two, not subtract. Subtracting a negative discount here
        # previously overstated shipping (7.2 - (-3.4) = 10.6 instead of the buyer's
        # actual 3.8).
        if not gross:
            return None
        gross_value = gross.get("value")
        if gross_value is None:
            return None
        discount_value = (discount or {}).get("value")
        net = float(gross_value) + float(discount_value or 0)
        return f"{net:.2f}"

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
        """Sell Inventory API bulkUpdatePriceQuantity, updating both this SKU's
        shipToLocationAvailability.quantity (inventory-item level) and, when a live
        offer exists for it, that offer's availableQuantity — unlike Etsy's
        updateListingInventory, this is a targeted partial update, not a full replace, so
        there's no GET-then-PUT round trip needed for the item level.

        eBay's own docs state the live listing quantity is min(item-level qty,
        offer-level availableQuantity) — item-level alone is sufficient to correctly
        drive a quantity DOWN to (and including) 0, but a later increase could be
        silently capped if the offer-level number was ever set independently at
        offer-creation time and never touched since. Updating both keeps them in sync in
        both directions. A literal 0 is natively accepted by eBay's Inventory API as the
        standard "out of stock" signal (confirmed via eBay's docs) — unlike Etsy, no
        special-casing is needed here for the zero value itself.

        Confirmed live: a SKU with no live offer (e.g. its listing was never migrated to
        an Inventory API object — see build_listing_sku_index's docstring) 404s on
        GET .../offer?sku=... with "This Offer is not available"; that's treated as "no
        offer to also update", not an error, since the item-level update alone is still
        valid and matches this method's pre-existing behavior for such SKUs.
        """
        if sku is None:
            raise PlatformSyncError("Cannot push a quantity update to eBay without a SKU")

        offer_ids = await self._resolve_offer_ids(session, connection, sku)

        request: dict = {"sku": sku, "shipToLocationAvailability": {"quantity": qty}}
        if offer_ids:
            request["offers"] = [{"offerId": offer_id, "availableQuantity": qty} for offer_id in offer_ids]

        body = {"requests": [request]}
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

    async def _fetch_offers(self, session, connection: PlatformConnection, sku: str) -> list[dict]:
        """GET .../offer?sku=... — the raw offer objects for one SKU.

        `sku` is a REQUIRED query parameter on getOffers and there is no bulk-offers
        endpoint (verified against eBay's docs), so anything needing offer data for many
        SKUs is unavoidably one call each — see _enrich_with_offers for how that's bounded.

        A 404 means no offer exists for this SKU (see push_listing_quantity's docstring),
        not an error; any other non-200 is a real failure."""
        response = await self._authed_request(
            session, connection, "GET", f"{self.api_base}/sell/inventory/v1/offer", params={"sku": sku}
        )
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise PlatformSyncError(f"Failed to look up eBay offers for SKU '{sku}': {response.status_code} {response.text}")
        return response.json().get("offers", [])

    async def _resolve_offer_ids(self, session, connection: PlatformConnection, sku: str) -> list[str]:
        """Offer ids for a SKU — the push path's view of _fetch_offers. A 404 yields an
        empty list rather than raising, since the item-level quantity update alone is
        still valid for a SKU with no live offer."""
        return [o["offerId"] for o in await self._fetch_offers(session, connection, sku) if o.get("offerId")]

    async def build_listing_sku_index(
        self,
        session,
        connection: PlatformConnection,
        *,
        enrich: bool = True,
        enrich_skus: set[str] | None = None,
    ) -> dict[str, ExternalListingRef]:
        """Sell Inventory API getInventoryItems, paginated — unlike Etsy, eBay's
        Inventory API is SKU-keyed natively, but this builds the same bulk dict-of-SKU
        shape as EtsyAdapter's version so the shared listing_sync service and its UI need
        no special-casing.

        getInventoryItems alone can't fill an ExternalListingRef: it carries no listing id
        and no listing state (those live on the associated Offer), so `enrich` runs a
        second pass over getOffers to populate state and variation properly. See
        _enrich_with_offers for the cost and how it's bounded.

        `enrich`/`enrich_skus` are FIDELITY HINTS, NOT FILTERS. The returned index always
        contains every SKU eBay reports, whatever they're set to — only how much detail a
        given entry carries varies. That's load-bearing: two callers use this purely for
        `sku in index` membership tests and would silently start missing SKUs if these
        narrowed the index itself.

        A SKU absent from this index most often means the listing it belongs to was
        created via eBay's Seller Hub UI (or the legacy Trading API) and was never
        migrated to an Inventory API object (eBay's `bulkMigrateListing`) — confirmed
        live via a direct GET on a specific missing SKU returning 404 on both
        getInventoryItem and getOffers, i.e. eBay genuinely has no Inventory API record
        for it, not a bug in this pagination/indexing. See _index_inventory_item's
        caller in listing_sync.py for where that shows up as "not found" without this
        context."""
        params: dict[str, str | int] = {"limit": _INVENTORY_PAGE_LIMIT, "offset": 0}
        index: dict[str, ExternalListingRef] = {}
        aspects_by_sku: dict[str, dict[str, list[str]]] = {}

        for _ in range(_MAX_PAGES):
            response = await self._authed_request(
                session, connection, "GET", f"{self.api_base}/sell/inventory/v1/inventory_item", params=params
            )
            if response.status_code != 200:
                raise PlatformSyncError(f"Failed to fetch eBay inventory items: {response.status_code} {response.text}")

            body = response.json()
            results = body.get("inventoryItems", [])
            for item in results:
                self._index_inventory_item(item, index, aspects_by_sku)

            total = body.get("total", len(results))
            params["offset"] = int(params["offset"]) + len(results)
            if not results or int(params["offset"]) >= total:
                break

        if enrich:
            targets = set(index) if enrich_skus is None else set(index) & enrich_skus
            await self._enrich_with_offers(session, connection, index, aspects_by_sku, targets)

        return index

    async def _enrich_with_offers(
        self,
        session,
        connection: PlatformConnection,
        index: dict[str, ExternalListingRef],
        aspects_by_sku: dict[str, dict[str, list[str]]],
        targets: set[str],
    ) -> None:
        """Fills in real listing id, listing state and variation from each SKU's Offer.

        One getOffers call per SKU — eBay requires `sku` on that endpoint and offers no
        bulk equivalent — so callers scope `targets` to the SKUs StockSmith actually
        tracks. A seller with 2,000 eBay SKUs and 80 in StockSmith pays 80 calls, not
        2,000.

        Never raises. A failure here degrades an entry's detail; it must not fail the
        whole index and repaint a catalogue as out-of-sync because one call timed out.
        """
        if not targets:
            return

        # Refresh once up front so the common case never contends on the refresh lock —
        # correctness doesn't depend on this (see _do_refresh), only latency does.
        await self._ensure_fresh(session, connection)

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_OFFER_LOOKUPS)

        async def _one(sku: str) -> tuple[str, list[dict]]:
            async with semaphore:
                return sku, await self._fetch_offers(session, connection, sku)

        results = await asyncio.gather(*(_one(sku) for sku in sorted(targets)), return_exceptions=True)

        offers_by_sku: dict[str, list[dict]] = {}
        for result in results:
            if isinstance(result, BaseException):
                # Deliberately leaves the pass-1 ref untouched (state stays "active", i.e.
                # "an inventory item exists"). Downgrading on a transport failure would let
                # one flaky moment mark the whole catalogue as not-live; only a definitive
                # 404 (an empty offer list, handled below) is treated as real information.
                logger.warning("eBay offer lookup failed during index enrichment: %s", result)
                continue
            sku, offers = result
            offers_by_sku[sku] = offers

        # Group by listing so variation can be narrowed to the aspects that actually vary
        # within a listing — see _varying_aspects.
        skus_by_listing: dict[str, list[str]] = {}
        for sku, offers in offers_by_sku.items():
            offer = self._select_offer(offers)
            listing_id = str((offer or {}).get("listing", {}).get("listingId") or "") or None
            if listing_id is not None:
                skus_by_listing.setdefault(listing_id, []).append(sku)

        varying_by_sku = self._varying_aspects(skus_by_listing, aspects_by_sku)

        for sku, offers in offers_by_sku.items():
            ref = index.get(sku)
            if ref is None:
                continue
            offer = self._select_offer(offers)
            if offer is None:
                # An inventory item with no offer is real information, not a failure: the
                # SKU exists on eBay but nothing is listed for sale. Surfaces downstream
                # as listing_not_active, which is strictly truer than the old hardcoded
                # "active".
                ref.state = "no_offer"
                continue
            # Deliberately NOT writing the real listingId onto ref.external_listing_id
            # yet, though it's right here: for eBay that field holding the SKU is a
            # documented invariant with a second writer (listing_adoption.apply_adoption
            # mirrors it, and docs/listing-adoption.md states it as a safety property).
            # Flipping it is a separate change so it stays revertible on its own.
            ref.state = self._offer_state(offer)
            ref.variation = self._format_variation(varying_by_sku.get(sku, {}))

    @staticmethod
    def _index_inventory_item(
        item: dict,
        index: dict[str, ExternalListingRef],
        aspects_by_sku: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        sku = item.get("sku")
        if not sku:
            return
        product = item.get("product") or {}
        availability = (item.get("availability") or {}).get("shipToLocationAvailability") or {}
        # An inventory item carries no listing id and no listing state — those live on the
        # associated Offer. This pass therefore sets the coarse "an inventory item exists"
        # placeholder of "active", which _enrich_with_offers replaces with the real state.
        # It's also what an entry keeps when enrichment is skipped or its lookup fails.
        #
        # `title` stays None when absent rather than "" — getInventoryItems (plural) is
        # known to omit the whole `product` container for some items that
        # getInventoryItem (singular) returns in full, and "" would defeat the UI's
        # `?? "—"` placeholder and render as a blank cell.
        index[sku] = ExternalListingRef(
            external_listing_id=sku,
            title=product.get("title"),
            sku=sku,
            state="active",
            quantity=int(availability.get("quantity", 0)),
            variation=None,
        )
        # Recorded even when empty: an item present in the response with no aspects is
        # different from one whose lookup never happened, and _varying_aspects needs the
        # whole listing group to tell which aspects actually vary.
        if aspects_by_sku is not None:
            aspects_by_sku[sku] = product.get("aspects") or {}

    @staticmethod
    def _select_offer(offers: list[dict]) -> dict | None:
        """Which offer represents the listing, when a SKU has several (a SKU can carry an
        auction and a fixed-price offer at once).

        Prefers a published offer that actually produced a listing, then any published
        one, then whatever exists — so a live listing is never passed over in favour of an
        unpublished draft."""
        if not offers:
            return None
        published = [o for o in offers if str(o.get("status", "")).upper() == "PUBLISHED"]
        with_listing = [o for o in published if (o.get("listing") or {}).get("listingId")]
        return (with_listing or published or offers)[0]

    @staticmethod
    def _offer_state(offer: dict) -> str:
        """Maps an offer to the same vocabulary EtsyAdapter uses for `state`, which
        listing_sync._status_from_match compares against "active"."""
        if str(offer.get("status", "")).upper() != "PUBLISHED":
            return "unpublished"
        listing_status = str((offer.get("listing") or {}).get("listingStatus", "")).upper()
        if not listing_status:
            return "active"  # published, but no listing container — trust `status`
        # Unknown values pass through lowercased rather than being forced to a known one:
        # eBay can add enum members, and inventing "active" for something unrecognised
        # would claim a listing is live on no evidence.
        return _EBAY_LISTING_STATES.get(listing_status, listing_status.lower())

    @staticmethod
    def _format_variation(aspects: dict[str, list[str]]) -> str | None:
        """Renders eBay aspects in the exact shape EtsyAdapter._format_variation produces
        ("Size: Large, Colour: Caramel"). The `variation` column is cross-platform and
        shown in one shared table, so the two must not drift.

        eBay's own aspect order is preserved rather than sorted — it matches how the
        listing itself reads, and sorting would reorder "Size, Colour" into "Colour, Size".
        """
        parts = [f"{name}: {', '.join(values)}" for name, values in aspects.items() if name and values]
        return ", ".join(parts) if parts else None

    @staticmethod
    def _varying_aspects(
        skus_by_listing: dict[str, list[str]], aspects_by_sku: dict[str, dict[str, list[str]]]
    ) -> dict[str, dict[str, list[str]]]:
        """Narrows each SKU's aspects to the ones that actually differ across the SKUs
        sharing its listing.

        An inventory item's aspects can carry common item specifics (Brand, Material)
        alongside the varying ones, and rendering "Brand: Acme" in a variation column
        would be wrong. eBay's authoritative answer lives on the inventory item group
        (variesBy.specifications), which is unreachable here: getInventoryItemGroup needs
        a group key that getInventoryItems doesn't return, and there's no list-groups
        endpoint. Deriving it degrades correctly either way — if eBay already returned
        only the varying aspects this is a no-op, and if it returned common ones too they
        get stripped.

        A single-SKU listing yields {} (hence no variation), matching Etsy, where a
        listing with one product and no property values gives variation=None.

        Caveat: when the caller scopes enrichment to a subset of SKUs, a listing group can
        be incomplete, so an aspect that varies only against an untracked sibling looks
        constant and is dropped. For a product whose variants are all tracked — the normal
        case — the group is complete.
        """
        varying: dict[str, dict[str, list[str]]] = {}
        for skus in skus_by_listing.values():
            if len(skus) < 2:
                for sku in skus:
                    varying[sku] = {}
                continue
            names = {name for sku in skus for name in aspects_by_sku.get(sku, {})}
            differing = {
                name
                for name in names
                # Compared as tuples so a multi-valued aspect is handled, and a SKU missing
                # the aspect entirely counts as a difference rather than being ignored.
                if len({tuple(aspects_by_sku.get(sku, {}).get(name, [])) for sku in skus}) > 1
            }
            for sku in skus:
                aspects = aspects_by_sku.get(sku, {})
                varying[sku] = {name: values for name, values in aspects.items() if name in differing}
        return varying

    async def fetch_classic_listings(
        self, session, connection: PlatformConnection
    ) -> list[ClassicListingCandidate]:
        """Trading API GetMyeBaySelling (ActiveList), paginated — surfaces every active
        listing regardless of Inventory API migration state, unlike build_listing_sku_index
        which only sees listings already migrated. This is the fetch side of the
        unmigrated-listing adoption feature: a classic listing with a SKU set in Seller
        Hub but never migrated shows up here even though it's invisible to
        build_listing_sku_index.

        Deliberately GetMyeBaySelling rather than GetSellerList: GetSellerList requires a
        StartTimeFrom/StartTimeTo window (max 120 days), which would silently miss any
        Good-'Til-Cancelled listing whose most recent renewal falls outside that window.
        GetMyeBaySelling's ActiveList returns every currently-active listing with no time
        window at all, matching what a seller sees under Seller Hub's own Active tab.

        The trade-off is that ActiveList is a summary view: it does not reliably include
        the <Variations> block, so the SKUs on a candidate returned here may be
        incomplete or empty even for a listing that has them. Candidates are therefore
        marked detail_loaded=False and eligibility deliberately withholds SKU-based
        judgements (see _evaluate_eligibility); anything that actually needs the real
        SKUs calls fetch_classic_listing_detail for the one listing concerned.

        UNVERIFIED against a live account, same caveat this class's docstring already
        carries for build_listing_sku_index/push_listing_quantity: the Trading API is a
        wholly different (XML/SOAP-era) surface from the Sell REST APIs used everywhere
        else in this file, and the field names parsed below have not been confirmed
        live. Budget Sandbox verification before trusting this blindly."""
        candidates: list[ClassicListingCandidate] = []
        page = 1

        for _ in range(_MAX_TRADING_PAGES):
            body = self._build_get_my_ebay_selling_xml(page)
            response = await self._authed_trading_request(session, connection, "GetMyeBaySelling", body)
            if response.status_code != 200:
                raise PlatformSyncError(
                    f"Failed to fetch eBay active listings: {response.status_code} {response.text}"
                )

            root = ElementTree.fromstring(response.text)
            _raise_for_trading_ack(root, "GetMyeBaySelling")

            active_list = root.find("e:ActiveList", _TRADING_NS)
            if active_list is None:
                break

            item_array = active_list.find("e:ItemArray", _TRADING_NS)
            items = item_array.findall("e:Item", _TRADING_NS) if item_array is not None else []
            for item in items:
                candidates.append(self._parse_classic_listing(item))

            pagination = active_list.find("e:PaginationResult", _TRADING_NS)
            total_pages = int(pagination.findtext("e:TotalNumberOfPages", "1", _TRADING_NS)) if pagination is not None else 1
            if not items or page >= total_pages:
                break
            page += 1

        # enrich=False: this index is used purely for `sku in migrated_index` membership
        # tests below, so per-SKU offer lookups would be pure waste on a user-facing picker.
        migrated_index = await self.build_listing_sku_index(session, connection, enrich=False)
        for candidate in candidates:
            candidate.is_migrated = any(sku in migrated_index for sku in candidate.skus)

        return candidates

    def _build_get_my_ebay_selling_xml(self, page: int) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<ActiveList>"
            "<Include>true</Include>"
            f"<Pagination><EntriesPerPage>{_TRADING_PAGE_LIMIT}</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>"
            "</ActiveList>"
            "<DetailLevel>ReturnAll</DetailLevel>"
            "</GetMyeBaySellingRequest>"
        )

    async def _resolve_site_id(self, session, connection: PlatformConnection) -> str:
        """The seller's own eBay site, needed for every Trading API call's SITEID header.

        Resolved via GetUser (which itself needs a SITEID header — chicken-and-egg, so
        the bootstrap call goes out under eBay's own default site; GetUser returns the
        user's registration site regardless of which site it's asked under). Any failure
        degrades to _DEFAULT_TRADING_SITE_ID rather than raising: a wrong-but-plausible
        site yields an empty listing list, which the caller surfaces as "no unmigrated
        listings found", whereas raising here would break the whole picker over a
        best-effort lookup."""
        if self._site_id is not None:
            return self._site_id

        try:
            response = await self._trading_request_once(
                session, connection, "GetUser", "<GetUserRequest xmlns=\"urn:ebay:apis:eBLBaseComponents\"/>",
                site_id=_DEFAULT_TRADING_SITE_ID,
            )
            if response.status_code == 200:
                root = ElementTree.fromstring(response.text)
                site_name = root.findtext("e:User/e:Site", "", _TRADING_NS)
                resolved = _TRADING_SITE_IDS.get(site_name)
                if resolved is not None:
                    self._site_id = resolved
                    logger.info("Resolved eBay Trading API site '%s' -> id %s", site_name, resolved)
                    return resolved
                logger.warning("eBay GetUser returned unmapped site '%s' — falling back to site id %s", site_name, _DEFAULT_TRADING_SITE_ID)
        except Exception:
            logger.warning("eBay GetUser site lookup failed — falling back to site id %s", _DEFAULT_TRADING_SITE_ID, exc_info=True)

        self._site_id = _DEFAULT_TRADING_SITE_ID
        return self._site_id

    def _trading_headers(self, connection: PlatformConnection, call_name: str, site_id: str) -> dict[str, str]:
        """The Trading API does NOT take the OAuth token as `Authorization: Bearer` the
        way every Sell REST call in this file does — per its own HTTP-headers table the
        user access token goes in X-EBAY-API-IAF-TOKEN, with RequesterCredentials
        omitted. Notably this needs no DevID/AppName/CertName either: those are
        "only required for calls that set up and retrieve a user's authentication
        token… In all other calls, this value is ignored", which is what makes the
        Trading API reachable at all with the credentials StockSmith already stores
        (PlatformAppCredential holds only client_id/secret/ru_name).

        Recorded in docs/plan-ebay-existing-store-onboarding.md, where establishing it
        was the single biggest risk to this whole approach clearing."""
        return {
            "X-EBAY-API-IAF-TOKEN": connection.access_token or "",
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-SITEID": site_id,
            "X-EBAY-API-COMPATIBILITY-LEVEL": _TRADING_COMPATIBILITY_LEVEL,
            "Content-Type": "text/xml",
        }

    async def _trading_request_once(
        self, session, connection: PlatformConnection, call_name: str, xml_body: str, site_id: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> httpx.Response:
        await self._ensure_fresh(session, connection)

        async def _send() -> httpx.Response:
            # Deliberately not routed through _request_once: that unconditionally sets
            # an Authorization: Bearer header, which is the wrong auth scheme here (see
            # _trading_headers).
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.request(
                    "POST",
                    self.trading_base,
                    headers=self._trading_headers(connection, call_name, site_id),
                    content=xml_body,
                )

        response = await _send()
        if response.status_code == 401:
            await self._do_refresh(session, connection)
            response = await _send()
        return response

    async def _authed_trading_request(
        self, session, connection: PlatformConnection, call_name: str, xml_body: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> httpx.Response:
        """Mirrors _authed_request's proactive-refresh, reactive-401-refresh and
        429/5xx backoff behaviour for the Trading API's XML surface. The transport differs
        on every other axis though: a different auth header (see _trading_headers), a raw
        XML string body instead of `json=`, failure reported in the body rather than the
        status (see _raise_for_trading_ack), and a per-seller SITEID resolved once per
        adapter.

        The 5xx retries do consume the Trading API's tight 5,000/day budget, unlike the
        Inventory API's — but only on a call that already failed, and capped at
        _MAX_SERVER_ERROR_RETRIES, so the worst case is a handful of extra calls on a day
        eBay is already unhealthy."""
        site_id = await self._resolve_site_id(session, connection)
        try:
            response = await self._trading_request_once(session, connection, call_name, xml_body, site_id, timeout)

            # Separate budgets — see _authed_request for why.
            rate_limit_attempts = 0
            server_error_attempts = 0
            while True:
                if response.status_code == 429 and rate_limit_attempts < _MAX_RATE_LIMIT_RETRIES:
                    delay = self._retry_delay(response, rate_limit_attempts)
                    logger.warning(
                        "eBay Trading API rate limited on %s (retry %d/%d in %.1fs)",
                        call_name,
                        rate_limit_attempts + 1,
                        _MAX_RATE_LIMIT_RETRIES,
                        delay,
                    )
                    rate_limit_attempts += 1
                elif response.status_code >= 500 and server_error_attempts < _MAX_SERVER_ERROR_RETRIES:
                    delay = self._retry_delay(response, server_error_attempts)
                    logger.warning(
                        "eBay Trading API server error %d on %s (retry %d/%d in %.1fs)",
                        response.status_code,
                        call_name,
                        server_error_attempts + 1,
                        _MAX_SERVER_ERROR_RETRIES,
                        delay,
                    )
                    server_error_attempts += 1
                else:
                    break
                await asyncio.sleep(delay)
                response = await self._trading_request_once(
                    session, connection, call_name, xml_body, site_id, timeout
                )
        except httpx.TimeoutException as e:
            # Same rationale as _authed_request's: an escaping httpx exception becomes an
            # opaque 500 with no guidance, and revise_listing_skus needs to catch this
            # specifically to warn that the change may have applied anyway.
            raise EbayTimeout(f"eBay did not respond in time for {call_name}") from e
        except httpx.HTTPError as e:
            raise PlatformSyncError(f"Could not reach eBay for {call_name}: {e}") from e

        if response.status_code == 429:
            raise PlatformRateLimitError("eBay Trading API rate limit exceeded")
        return response

    @staticmethod
    def _parse_classic_listing(item: ElementTree.Element, detail_loaded: bool = False) -> ClassicListingCandidate:
        item_id = item.findtext("e:ItemID", "", _TRADING_NS)
        title = item.findtext("e:Title", "", _TRADING_NS)
        listing_type = item.findtext("e:ListingType", "", _TRADING_NS)
        quantity_raw = item.findtext("e:SellingStatus/e:QuantityAvailable", "0", _TRADING_NS) or item.findtext(
            "e:Quantity", "0", _TRADING_NS
        )
        quantity = int(quantity_raw) if quantity_raw and quantity_raw.isdigit() else 0

        variations_el = item.find("e:Variations", _TRADING_NS)
        skus: list[str] = []
        variation_specifics: list[dict[str, str]] | None = None
        if variations_el is not None:
            variation_specifics = []
            for variation in variations_el.findall("e:Variation", _TRADING_NS):
                sku = variation.findtext("e:SKU", "", _TRADING_NS)
                skus.append(sku)
                specifics: dict[str, str] = {}
                for nvl in variation.findall("e:VariationSpecifics/e:NameValueList", _TRADING_NS):
                    name = nvl.findtext("e:Name", "", _TRADING_NS)
                    value = nvl.findtext("e:Value", "", _TRADING_NS)
                    if name:
                        specifics[name] = value
                variation_specifics.append(specifics)
        else:
            sku = item.findtext("e:SKU", "", _TRADING_NS)
            if sku:
                skus.append(sku)

        return ClassicListingCandidate(
            external_listing_id=item_id,
            title=title,
            listing_type=listing_type,
            # Variation SKUs are kept positionally aligned with variation_specifics (an
            # empty string marks a variation with no SKU, which _evaluate_eligibility
            # counts) — only the single-SKU path filters, since there's no alignment to
            # preserve there.
            skus=skus if variation_specifics is not None else [s for s in skus if s],
            variation_specifics=variation_specifics,
            quantity=quantity,
            is_migrated=False,  # filled in by the caller cross-referencing build_listing_sku_index
            ineligibility_reasons=_evaluate_eligibility(listing_type, skus, variation_specifics, detail_loaded),
            detail_loaded=detail_loaded,
        )

    async def fetch_classic_listing_detail(
        self, session, connection: PlatformConnection, external_listing_id: str
    ) -> ClassicListingCandidate:
        """Trading API GetItem for one listing — the authoritative source for a
        listing's SKU(s) and per-variation specifics.

        Needed because GetMyeBaySelling's ActiveList is a summary view that does not
        reliably return the <Variations> block, so fetch_classic_listings alone cannot
        be trusted to know a multi-variation listing's SKUs. Every path that actually
        depends on those SKUs (variation mapping, SKU alignment, adoption) calls this
        for the single selected listing instead of trusting the list payload — which is
        also far cheaper than re-running the whole paginated list crawl.

        UNVERIFIED against a live account — same caveat as fetch_classic_listings."""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            f"<ItemID>{_xml_escape(external_listing_id)}</ItemID>"
            "<IncludeVariations>true</IncludeVariations>"
            "<DetailLevel>ReturnAll</DetailLevel>"
            "</GetItemRequest>"
        )
        response = await self._authed_trading_request(session, connection, "GetItem", body)
        if response.status_code != 200:
            raise PlatformSyncError(
                f"Failed to fetch eBay listing {external_listing_id}: {response.status_code} {response.text}"
            )

        root = ElementTree.fromstring(response.text)
        _raise_for_trading_ack(root, "GetItem")

        item = root.find("e:Item", _TRADING_NS)
        if item is None:
            raise PlatformSyncError(f"eBay GetItem returned no Item for listing {external_listing_id}")
        return self._parse_classic_listing(item, detail_loaded=True)

    @staticmethod
    def _build_revise_skus_xml(candidate: ClassicListingCandidate, new_skus: list[str]) -> str:
        """Builds the ReviseFixedPriceItem body that rewrites this listing's SKU(s).

        SAFETY: for a multi-variation listing, eBay treats an omitted variation as one to
        DELETE, so every variation must be echoed back, each identified by its
        VariationSpecifics (the immutable identity of a variation — the SKU itself is
        just a mutable attribute of it, which is exactly why it can be rewritten this
        way). `new_skus` is therefore required to be positionally aligned with, and the
        same length as, candidate.variation_specifics; the caller is responsible for
        that and _align_new_skus enforces it.

        Pure and static so the generated XML — the part that could silently destroy
        variations if it were wrong — is unit-testable without a network call."""
        item_id = _xml_escape(candidate.external_listing_id)

        if candidate.variation_specifics is None:
            return (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
                f"<Item><ItemID>{item_id}</ItemID><SKU>{_xml_escape(new_skus[0])}</SKU></Item>"
                "</ReviseFixedPriceItemRequest>"
            )

        variations_xml = []
        for specifics, new_sku in zip(candidate.variation_specifics, new_skus):
            name_values = "".join(
                f"<NameValueList><Name>{_xml_escape(name)}</Name><Value>{_xml_escape(value)}</Value></NameValueList>"
                for name, value in specifics.items()
            )
            variations_xml.append(
                f"<Variation><SKU>{_xml_escape(new_sku)}</SKU>"
                f"<VariationSpecifics>{name_values}</VariationSpecifics></Variation>"
            )

        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            f"<Item><ItemID>{item_id}</ItemID>"
            f"<Variations>{''.join(variations_xml)}</Variations>"
            "</Item>"
            "</ReviseFixedPriceItemRequest>"
        )

    async def revise_listing_skus(
        self, session, connection: PlatformConnection, candidate: ClassicListingCandidate, new_skus: list[str]
    ) -> None:
        """Rewrites a CLASSIC (not-yet-migrated) listing's SKU(s) via Trading API
        ReviseFixedPriceItem, so StockSmith's own SKUs are what the listing carries into
        migration.

        This is deliberately done BEFORE migration rather than after. eBay's Inventory
        API keys inventory_item and offer objects *by SKU in the URL path*, so there is
        no documented rename operation once migrated — the only known route would be
        create-new / repoint-offer / delete-old, and deleteInventoryItem also deletes the
        associated offers and ends the live listing. Revising a classic listing's SKU, by
        contrast, is an ordinary non-destructive edit that sellers make routinely through
        Seller Hub's own bulk editor: the listing keeps its ItemID, watchers, and sales
        history. Doing the alignment on this side of migration therefore avoids the risky
        operation entirely, and the migrated objects get created with the right SKUs from
        the start rather than needing surgery afterwards.

        UNVERIFIED against a live account — same caveat as fetch_classic_listings."""
        if candidate.is_migrated:
            raise PlatformSyncError(
                f"Listing {candidate.external_listing_id} is already migrated — its SKUs can no longer be safely "
                "revised through this path (see this method's docstring)."
            )
        aligned = _align_new_skus(candidate, new_skus)
        body = self._build_revise_skus_xml(candidate, aligned)
        try:
            response = await self._authed_trading_request(
                session, connection, "ReviseFixedPriceItem", body, timeout=_REVISE_TIMEOUT
            )
        except EbayTimeout as e:
            # Same reasoning as migrate_listing's timeout: the revision may well have
            # applied. Re-running is safe because alignment is a no-op once the SKUs
            # already match (see listing_adoption.plan_sku_alignment).
            raise PlatformSyncError(
                f"eBay did not respond within {int(_REVISE_TIMEOUT)}s while rewriting SKUs on listing "
                f"{candidate.external_listing_id}. The change may still have applied — run this again to "
                "check. Re-running is safe: it does nothing if the SKUs already match."
            ) from e
        if response.status_code != 200:
            raise PlatformSyncError(
                f"Failed to revise SKUs on eBay listing {candidate.external_listing_id}: "
                f"{response.status_code} {response.text}"
            )
        _raise_for_trading_ack(ElementTree.fromstring(response.text), "ReviseFixedPriceItem")

    async def migrate_listing(
        self, session, connection: PlatformConnection, external_listing_id: str
    ) -> MigrationResult:
        """Sell Inventory API bulkMigrateListing — migrates a classic (Trading API /
        Seller Hub) listing into the Inventory API, creating inventory_item/offer
        objects for each of its SKUs. This is a modern Sell REST API (unlike
        GetMyeBaySelling above), so it reuses the normal JSON _authed_request path.

        Idempotent by design: migration is IRREVERSIBLE on eBay's side, so a caller that
        fails partway (e.g. the local Listing write blows up after eBay has already
        migrated) must be able to simply retry the whole operation. An
        "already migrated" rejection is therefore treated as success — see
        _is_already_migrated_error — with the resulting SKUs read back from the
        Inventory API instead of from the migration response.

        UNVERIFIED against a live account (no listing has been migrated through this
        code yet) — same caveat as fetch_classic_listings above."""
        body = {"requests": [{"listingId": external_listing_id}]}
        try:
            response = await self._authed_request(
                session,
                connection,
                "POST",
                f"{self.api_base}/sell/inventory/v1/bulk_migrate_listing",
                json=body,
                timeout=_MIGRATION_TIMEOUT,
            )
        except EbayTimeout as e:
            # eBay very likely carried on and finished after we stopped listening, so
            # this is emphatically NOT "nothing happened" — saying so would invite the
            # user to assume the listing is untouched. Re-running is the right move and
            # is safe: an already-migrated listing is treated as success (see this
            # method's docstring), which is precisely what makes a timeout recoverable.
            raise PlatformSyncError(
                f"eBay did not respond within {int(_MIGRATION_TIMEOUT)}s while migrating listing "
                f"{external_listing_id}. The migration may still have completed on eBay's side — "
                "run this again to check and finish linking. Migrating twice is safe."
            ) from e
        if response.status_code != 200:
            raise PlatformSyncError(f"Failed to migrate eBay listing: {response.status_code} {response.text}")

        result = response.json()
        matched = next((r for r in result.get("responses", []) if str(r.get("listingId")) == str(external_listing_id)), None)
        if matched is None:
            raise PlatformSyncError(
                f"eBay bulk_migrate_listing response did not include listing '{external_listing_id}'"
            )
        status_code = matched.get("statusCode")
        if status_code is not None and not (200 <= status_code < 300):
            if not _is_already_migrated_error(matched):
                raise PlatformSyncError(
                    f"eBay rejected migration of listing '{external_listing_id}': {status_code} {matched.get('errors')}"
                )
            logger.info(
                "eBay listing %s was already migrated — treating as success and reading back its SKUs",
                external_listing_id,
            )
            detail = await self.fetch_classic_listing_detail(session, connection, external_listing_id)
            return MigrationResult(
                external_listing_id=external_listing_id,
                inventory_item_skus=[s for s in detail.skus if s],
                raw=matched,
            )

        inventory_item_skus = [
            sku_result.get("sku")
            for sku_result in matched.get("inventoryItems", [])
            if sku_result.get("sku") and (sku_result.get("statusCode") is None or 200 <= sku_result["statusCode"] < 300)
        ]
        return MigrationResult(
            external_listing_id=external_listing_id,
            inventory_item_skus=inventory_item_skus,
            raw=matched,
        )
