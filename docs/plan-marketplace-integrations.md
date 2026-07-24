# StockSmith — Marketplace Integrations & Order Lifecycle: Planning Pass

## Status

Planning only — nothing in this document has been implemented. Written after reading the
current backend (`app/services/platforms/*`, `app/services/order_sync.py`,
`app/services/allocation.py`, `app/services/kitting.py`, `app/models/*`), the frontend
Settings page, and — for the root-cause section below — the actual installed app's
`backend.log` and SQLite database at `%LOCALAPPDATA%\StockSmith\`.

**Context that changes the shape of this plan**: the codebase is far more built-out than
"add marketplace integrations" implies. Etsy OAuth, order pull-sync (preview + commit),
per-line allocation, kitting/packaging reservation, an `EbayAdapter` that already mirrors
`EtsyAdapter` end-to-end, and the core "available quantity" computation
(`kitting.compute_max_sellable`) all already exist. What's genuinely missing is narrower
than the original ask suggests: a way for the packaged desktop app to hold platform
credentials, a sync scheduler, the *outbound* quantity-push leg, eBay's Sandbox
verification, and the cancellation/return workflow. The plan below is scoped
accordingly — it builds on existing code, not around it.

**Privacy default**: both platform integrations are designed, by default, to never persist
marketplace-buyer-identifying data (name, username, address) — see Sections 1e and 2. This
sidesteps eBay's Marketplace Account Deletion notification requirement entirely (the
exemption is available once nothing identifying is stored) rather than standing up new
public infrastructure to satisfy it, and keeps Etsy and eBay consistent with each other.

---

## 0. Root cause: why Etsy is currently broken

**Symptom** (from the installed app's own log, `%LOCALAPPDATA%\StockSmith\backend.log`):
every `POST /api/v1/platforms/etsy/preview-sync` call returns `400 Bad Request`.

**What it's NOT**: I queried the installed app's live `platform_connections` row directly.
It has a non-null `access_token` and `refresh_token`, a `last_refreshed_at` from earlier
the same day, and an `access_token_expires_at` exactly one hour after that (Etsy's normal
token TTL) — i.e. the stored OAuth grant looks completely healthy, not expired or revoked.
A dead/revoked token would surface as `401` (`PlatformAuthError` → `_map_platform_error`
in `routers/platforms.py`), not `400`.

**What it IS**: tracing every place `preview-sync`'s call path can raise `400`:

- `order_sync._get_connection` → `400 "{platform} is not connected"` if `refresh_token`
  is `None`. Ruled out — the DB shows a token.
- `app/services/platforms/__init__.py:20-24` — `get_adapter()` → `400 "Etsy is not
  configured — set etsy_client_id/etsy_client_secret"` whenever
  `settings.etsy_client_id`/`etsy_client_secret` are unset. **This is the one.**

`Settings` (`app/config.py`) reads `etsy_client_id`/`etsy_client_secret`/
`public_base_url` from a `.env` file. In the packaged desktop app:

- `app/bootstrap.py` (`run()`, lines 101-122) only injects `DATABASE_URL`, `ASSET_ROOT`,
  `SHARED_PASSWORD_HASH`, and `TOKEN_ENCRYPTION_KEY` into the process environment before
  the backend starts. It never sets `ETSY_CLIENT_ID`/`ETSY_CLIENT_SECRET`/
  `PUBLIC_BASE_URL` or the eBay equivalents.
- `stocksmith-backend.spec`'s PyInstaller `datas` list bundles `alembic.ini`/`alembic/*`
  only — no `.env`. (`.env` is also gitignored, so it wouldn't ship in a public installer
  even if someone tried.)
- Confirmed empirically: no `.env` anywhere under `%LOCALAPPDATA%\StockSmith\`, and no
  matching `ETSY_*`/`EBAY_*` system environment variables.

So in the installed app, `settings.etsy_client_id` is always `None`, and `get_adapter()`
400s immediately for **every** platform operation — connect, status-refresh, preview-sync,
sync-orders, check-sync — regardless of how healthy the stored connection is.

**Why the connection still looks "connected" with a recent token refresh, then**: this
StockSmith install was migrated from the original Postgres/"home-base" architecture
(`docs/plan-phase0-phase1.md`) via `backend/scripts/migrate_pg_to_sqlite.py`, which copies
`platform_connections` — encrypted tokens, timestamps, everything — byte-for-byte into the
new packaged app's SQLite file. That data is real and was current as of the *dev* backend
(which reads `backend/.env`, where real Etsy credentials do live) — it's just frozen,
because the packaged app can never reach the code path that would refresh it again.

**Conclusion**: this is a packaging/config gap, not an Etsy-side expired/revoked token,
scope change, deprecated endpoint, or rate limit. The adapter code itself (OAuth refresh,
receipt parsing, SKU indexing) looks correct and was working right up until the switch to
the self-contained distribution. **Fix = give the packaged app somewhere to hold platform
credentials at runtime** (Section 1a). A live call to confirm this 100% was blocked by
this session's sandboxing — worth a direct confirmation once that settings field exists:
entering the same Etsy Client ID/Secret from the dev `.env` should make sync work
immediately, with no reconnect needed.

---

## 1. Etsy — fix + move to auto-sync

### 1a. Fix: platform credentials need a home in the packaged app

A single-user desktop app has no build pipeline to inject secrets per-install and no
`.env` file a user would ever edit by hand. Recommend storing `etsy_client_id`/
`etsy_client_secret`/`ebay_client_id`/`ebay_client_secret` (and, for eBay, a
sandbox/production pair — see 2) as **encrypted DB columns**, editable from Settings →
Integrations, reusing the `EncryptedString`/Fernet infrastructure that already protects
OAuth tokens (`app/services/crypto.py`). A new small table (`PlatformAppCredential` or
similar) fits better than extending `PlatformConnection`, since these are pre-connection,
install-level app settings, not per-OAuth-grant state.

**Redirect URI**: the README already flags this as a known limitation ("assumes a stable,
externally-resolvable URL... needs to be verified against each platform's actual
constraints"). Two paths:
- Register `http://127.0.0.1:8000/api/v1/platforms/etsy/callback` directly as the app's
  redirect URI in Etsy's developer console — Etsy validates an exact string match, not a
  scheme restriction, so a loopback address may simply work. Cheapest to try first (no new
  infrastructure) — recommend a 5-minute empirical check early in Stage 1's build order.
- If Etsy's console rejects a non-https redirect at registration time, fall back to a
  small hosted relay that 302s to the loopback callback.

eBay is different: its `redirect_uri` is an opaque RuName registered once in the dev
portal (`routers/platforms.py::_redirect_uri` already handles this distinctly) — the real
callback URL is entered directly into that portal form, so the loopback-vs-https question
is moot for eBay's OAuth *redirect*. It resurfaces, unavoidably, for the Marketplace
Account Deletion endpoint (Section 2).

### 1b. Auto-sync scheduler

Replace the current manual "Preview sync / Sync now" buttons (`PlatformSyncPanel.tsx`)
with a background interval loop:

- `etsy_poll_interval_minutes` already exists in `config.py` (default 15, currently
  unused). Move it from an env-only global to a **per-connection, DB-editable value**
  (`PlatformConnection.sync_interval_minutes`), same pattern already used for
  `sync_start_date`. Default 15 min for both Etsy and eBay initially; independently
  configurable per platform in case rate-limit/volume needs diverge later.
- Implementation: a lightweight in-process `asyncio` loop per connected platform, started
  in FastAPI's lifespan/startup hook — `while True: await sleep(interval); try: await
  commit_sync(...) except: log`. No need for APScheduler/Celery in a single-process
  desktop app; keep it dependency-light.
- **Concurrency**: guard with a per-platform `asyncio.Lock` so a background tick and a
  manual "Sync now" click can't run `commit_sync` concurrently. (The DB's unique
  `(platform, external_order_id)` constraint would catch a raw duplicate-insert race, but
  a lock avoids wasted API calls and confusing partial-failure log noise.)
- **Auto-disable on repeated auth failure**: if a `PlatformAuthError` (revoked/expired
  token past refresh) happens N times in a row, stop retrying every interval and surface a
  persistent "reconnect needed" banner instead of hammering a dead connection forever.
- Unattended trust: several of `EtsyAdapter`'s own docstrings note the parsing was
  "defensively... since Etsy's exact response schema wasn't verifiable against live docs
  while building this... specifically so a human can sanity-check the parsing... before
  this is ever trusted to run unattended." Recommend a manual-preview burn-in period
  (part of Stage 1's build order below) before flipping auto-sync on by default, rather
  than trusting the parser unattended on day one.

### 1c. "Last successful sync" under auto-sync

Good news: the failure-isolation the user asked for **already exists**.
`PlatformConnection.last_orders_synced_at` (the watermark) is only ever advanced inside
`commit_sync`'s success path (`order_sync.py:211-223`); the `except` branch
(`_record_failure`) never touches it — a failed attempt genuinely cannot silently move
this timestamp forward today.

The gap is narrower: `PlatformStatus` only surfaces the watermark, not "did the *most
recent* attempt succeed." Recommend adding `last_sync_attempt_at` /
`last_sync_success_at` / `last_sync_error` to `PlatformStatus`, derived from the most
recent `PlatformSyncRun` row (already logged on both success and failure via
`_record_failure`) — so a failing auto-sync cycle is visible in Settings without paging
through the sync-run log table.

### 1d. Push inventory quantities to Etsy

The "what should be sellable" computation **already exists and already implements both
rules the user asked for**:

- `kitting.compute_max_sellable` returns `free_stock = current_stock - allocated_qty`
  (clamped by packaging capacity and `platform_ceiling_qty`) — an allocated-but-unshipped
  unit is already excluded, satisfying "allocated ⇒ unavailable" as-is.
- Material consumption already flows through the same path via
  `compute_variant_kitting_capacity`/buildability's material-based `max_buildable`, which
  read `material.current_qty - material.allocated_qty`.

What's missing is purely the **outbound push**: both adapters' `push_listing_quantity` are
`NotImplementedError` stubs, and `Listing.ceiling_qty`/`last_synced_qty`/`last_synced_at`
are explicitly reserved-but-unused columns. `kitting.sync_listing_ceiling_qty` already
writes those columns *locally* — extend that exact function to actually call
`adapter.push_listing_quantity(...)` and only stamp `last_synced_qty`/`last_synced_at` on
real success.

- **What to push**: `max_sellable` (truly on-hand right now), never
  `expected_max_sellable` (counts on-order/not-yet-received stock) — pushing "expected"
  would let a marketplace sell something not physically in hand.
- **Triggers**: any mutation to `Product.current_stock`/`allocated_qty`,
  `Material.current_qty`/`allocated_qty`, or `platform_ceiling_qty` should enqueue a
  recompute+push for that product/variant's `Listing` rows. Recommend **centralizing**
  this as a small debounced queue (coalesce bursts — e.g. importing 20 orders in one sync
  shouldn't fire 20 separate Etsy calls for the same listing) rather than scattering push
  calls across every call site (`allocation.py`, `kitting.py`, `stock_adjustments.py`,
  `builds.py`). Recompute the quantity **at send time**, not at enqueue time, so a stale
  precomputed value never overwrites a more recent change made while the item sat queued.
- **New scope required**: Etsy's `updateListingInventory` (`PUT
  /v3/application/listings/{listing_id}/inventory`) requires `listings_w`, which
  `routers/platforms.py`'s scope table deliberately omits today ("nothing writes listings
  yet"). Existing connections will need to reconnect once this scope is added — Etsy
  scopes are fixed at grant time.
- **Push failure handling** (explicitly asked about):
  1. Retry with the same short backoff pattern already used for 429s in
     `_authed_request`.
  2. On persistent failure, **do not block the next sync cycle** — order sync and
     quantity push are independent concerns; a stuck listing push shouldn't stop new
     orders from being pulled.
  3. Do surface it, though — a silently-stale Etsy quantity is a real overselling risk,
     not just cosmetic staleness. Log to a small push-outcome table (see Data Model
     section) and show a warning badge on the affected product.
  4. Never let a push failure roll back or block the local allocation/stock change —
     local inventory stays authoritative even if the marketplace echo lags.

### 1e. Stop persisting marketplace buyer identity (privacy default, consistency with eBay)

Etsy carries no equivalent of eBay's Marketplace Account Deletion requirement, but it's
worth adopting the same minimal-data stance for both, on the same reasoning the codebase
already applies to OAuth tokens (`crypto.py`'s `EncryptedString` docstring: encrypt tokens
"independent of app-level access control... to limit blast radius if the database itself
is ever exposed"). A buyer's name is exactly that kind of data — no functional part of
inventory/BOM/allocation logic reads it.

Checked what's actually persisted today: `ExternalOrder.raw` (which contains the full
receipt, including buyer info) is **never written to the database** for either platform —
it only flows through the ephemeral `preview-sync` response, which is never stored. The
only buyer-identifying thing that lands in the `orders` table is the parsed
`buyer_name`/`buyer_note` string, set once in `order_sync._upsert_order` at order
creation. So the change is small and surgical:

- In `_upsert_order`, stop setting `Order.buyer_name`/`buyer_note` from `ext_order` for
  **both** platforms. Leave them `None` for synced orders.
- `ExternalOrder.buyer_name`/`buyer_note` (the dataclass fields) stay as-is — they're
  still useful for the ephemeral, never-persisted preview view, where showing a name for a
  moment during a human review step is fine.
- `Order.buyer_name`/`buyer_note` as *columns* stay — they're still meaningful for
  manually-created orders, where the user is typing in their own note about their own
  order, not receiving marketplace PII via an API.
- Net effect: the order list/detail UI shows `external_order_id` (StockSmith's own
  reference number, not PII) instead of a name for synced orders. To look up who an order
  is actually for, click through to the receipt on Etsy/eBay's own site using that ID —
  a minor workflow change for a single-shop tool, in exchange for a materially smaller
  privacy footprint and (for eBay) skipping the deletion-notification build entirely.

---

## 2. eBay — new, mirrors Etsy

**This is materially less "new" than it sounds.** `app/services/platforms/ebay.py`
already implements the full `PlatformAdapter` protocol end-to-end: OAuth (standard
authorization-code grant, not PKCE — correctly modeled as different from Etsy),
`fetch_orders_since` via the Sell Fulfillment API, `build_listing_sku_index` via the Sell
Inventory API, fee/payment lookup via Sell Finances. It's already registered in
`get_adapter()`, already has scopes declared in `routers/platforms.py`, and the frontend's
`PlatformIntegrationCard`/`PlatformSyncPanel`/`CONNECTABLE_PLATFORMS` are already
platform-parametrized (not Etsy-specific) — so eBay already renders a working-looking
Settings card today (modulo Section 0's credential gap, which blocks it identically to
Etsy).

**What's genuinely unbuilt:**

1. **Never verified against a live account.** The adapter's own docstring says so
   explicitly: "no live-connected eBay shop was available to verify against while
   building this... treat field parsing as best-effort." This needs a real Sandbox pass
   before it's trustworthy, exactly like Etsy's own docstrings flag for itself.
2. `push_listing_quantity` — same `NotImplementedError` stub as Etsy; same design as
   Section 1d, generalized (see Section 3).
3. Write scope for inventory push is also omitted from `_SCOPES` today.
4. Section 0's packaged-app credential gap blocks eBay identically to Etsy.
5. **Marketplace Account Deletion/Closure notifications — resolved by declaring
   exemption, not by building infrastructure** (below). This was flagged as a hard
   blocker in the original ask; it no longer is, given the privacy-default in 1e/below.
6. Sandbox vs. Production keysets aren't modeled anywhere — `config.py` has one
   `ebay_client_id`/`secret` pair; `EbayAdapter`'s `AUTHORIZE_URL`/`TOKEN_URL`/
   `API_BASE`/`IDENTITY_BASE` are hardcoded to production hosts with no sandbox
   equivalents (`api.sandbox.ebay.com`, etc.).
7. **Deliberately not adding a ship-to address.** `EbayAdapter._parse_order` only
   captures `buyer.username` today, and that's staying that way — see below. eBay
   fulfillment (printing labels, entering addresses) happens in eBay's own seller tools,
   outside StockSmith, same as it already effectively does today (StockSmith has no
   label-printing feature at all currently).

### Marketplace Account Deletion notifications — default: claim the exemption, build nothing

eBay requires every application to either subscribe to these notifications or self-certify
that it doesn't retrieve/store eBay members' personal data. That declaration is
all-or-nothing per application (client ID) — if *any* part of the app persists a member's
name, username, or address obtained via eBay's API, the exemption isn't available and the
endpoint becomes mandatory.

**Default recommendation: qualify for the exemption, deliberately.** Concretely:

- `EbayAdapter` should keep parsing `buyer.username` into `ExternalOrder.buyer_name` for
  the ephemeral, never-persisted preview view (useful for a human sanity-checking a sync
  before committing) — but per 1e, `order_sync._upsert_order` never writes it to the
  `orders` table for eBay (or Etsy) orders.
- No ship-to address is ever fetched or stored (item 7 above) — eBay fulfillment stays
  outside StockSmith.
- With those two things true, self-certify "does not store eBay members' personal data"
  in the eBay developer portal's compliance settings. No verification token, no public
  endpoint, no always-on infrastructure, no challenge-response code to write and host.
- **This is the whole point of doing 1e for both platforms rather than just eBay**: the
  moment *any* eBay-sourced buyer data lands in the `orders` table, the exemption is gone
  and the endpoint becomes mandatory again — so the "never persist it" rule has to be
  airtight across every code path that writes an eBay order, not just a policy on paper.
- Trade-off (repeated from 1e, since it matters most here): no in-app "who is this order
  for" for eBay orders. Click through via `external_order_id` to eBay's own order page
  when you actually need the buyer's identity. If in-app packing/labels ever becomes a
  real feature you want, that decision needs revisiting — it would reintroduce address
  storage and, with it, the mandatory notification endpoint.

**If this trade-off ever stops being acceptable** (e.g. you decide you want in-app
shipping labels after all), the fallback is a minimal stateless serverless function —
Cloudflare Workers is the easiest zero-to-running path for a non-developer: free tier, a
public `https://*.workers.dev` HTTPS URL from the browser-based editor with no CLI/domain
setup, and the function itself is ~15 lines (answer eBay's `GET ?challenge_code=...` with
`sha256(challengeCode + verificationToken + endpointURL)`; on the real `POST`
notification, acknowledge and forward the payload somewhere visible — a webhook or email —
since StockSmith itself isn't running 24/7 to act on it live). Keeping this documented
here rather than building it now.

---

## 3. Cross-platform inventory sync

**The single source of truth already exists and is already platform-agnostic.**
`Product.current_stock`/`allocated_qty` (and `Material.current_qty`/`allocated_qty` for
kitting) carry no platform dimension at all — only `Listing` rows point back to a
product/variant per platform. An order from *either* platform already decrements the
*same* fields via the same `allocation.allocate_order`/`ship_order` functions, keyed only
on `Order.platform` for bookkeeping, not for which stock pool it draws from. So "an item
sold on either platform reduces inventory reflected on both" is **already true locally**
today — the only missing piece is the outbound push (Sections 1d/2), applied to every
platform that has a `Listing` for the affected product.

**Race conditions / oversell risk**: allocation already happens at order-import time, not
at the end of a sync batch (`order_sync._reconcile_status` calls `allocate_order`
immediately per new order) — so the free-stock number updates in-process the moment
StockSmith learns about a sale. The real oversell window is therefore "how long between
the sale happening on platform A and StockSmith's *sync* noticing it," not any missing
locking inside StockSmith. Two-platform double-sell can't be fully eliminated by
StockSmith alone (each marketplace only stops selling once *its own* last-known quantity
hits 0) — the actual mitigation is shrinking that window:

- **Push immediately on allocation**, not on the next general sync interval (this is
  exactly Section 1d's debounced-push design — no separate mechanism needed, just confirm
  it fires for quantity-affecting events specifically, decoupled from the order-*pull*
  interval).
- The general order-sync interval (pulling *new* orders) can stay on its own cadence
  (e.g. 15 min) — that side isn't the oversell-critical path. The oversell-critical path
  is "local stock changed → tell both marketplaces fast," which is a push concern, not a
  pull-frequency concern.
- When a genuine oversell does happen anyway (both platforms accept the last unit before
  either sync catches up), that's not a new failure mode — allocation simply grants 0 (free
  stock already 0), and the order sits partially/un-allocated, which existing
  dashboard-style views (`get_orders_awaiting_inventory`'s packaging analog) already
  surface. The user resolves it manually (cancel the later order, offer a backorder).
  This should stay visible, not silently auto-resolved.

---

## 4. Order cancellation & returns workflow

This section is the most net-new. `POST /orders/{id}/cancel` (`allocation.cancel_order`)
exists today but: (a) unconditionally deallocates every line back to free stock with no
scrap/return choice, and (b) **hard-blocks** (`409`) cancelling any order with
`shipped_qty > 0` ("process a return instead") — and no return flow exists at all.
`order_sync._reconcile_status` also **auto-applies** cancellation the instant Etsy reports
`is_cancelled=true`, with no confirmation step — the opposite of what's being asked for.

### Design

**Not-yet-shipped cancel** (today's only supported case): for each line with
`allocated_qty > 0`, prompt scrap vs. return-to-stock per line.
- *Return to stock* = today's existing behavior unchanged (deallocate; free stock goes
  back up).
- *Scrap* = deallocate the reservation **and** write off the stock — reduce
  `current_stock` via a stock-adjustment-equivalent (audited), not just a deallocation.

**Already-shipped return** (today's `409` case — needs to become real): receive back
some/all `shipped_qty`, then run the same per-line scrap/return prompt — but **the
default differs by what's being returned, not by shipment status alone**:
- The **finished product unit itself** (`Product.current_stock`) is a legitimate
  "return to stock" candidate — it's a whole, resellable item coming back. Default:
  return to stock (user can override to scrap if damaged/unsellable).
- Its **Kitting-BOM packaging** (box, label — already consumed via
  `OrderKittingAllocation.consumed_qty` at ship time) genuinely can't be un-consumed.
  Default: scrap. This matches the user's explicit ask ("Kitting BOM materials should
  default to scrap... since they're already assembled/consumed").
- Build-BOM raw materials were already consumed earlier, at *build* time (via
  `create_build`), not at ship time — they're not part of this decision at all; only the
  finished product and its kitting/packaging are in play at return time.

**New audit trail**: an `order_line_returns` (or similarly named) record — line_id, qty,
disposition (scrap / return_to_stock), source (cancel_before_ship / return_after_ship),
reason, created_at — is the "scrap/return history" the user asked to be able to see.

**Etsy cancellation detection — stop auto-applying**: change
`order_sync._reconcile_status` so a newly-`is_cancelled` order sets a visible
pending-decision flag instead of silently calling `allocation.cancel_order`. Recommend
either a structured boolean (`Order.pending_marketplace_cancellation`) or extending the
existing free-text `sync_issue` convention already used for the very similar "Etsy shows
shipped but nothing allocated" self-healing case — surfaced on the dashboard and order
detail so the user explicitly runs the (now scrap/return-aware) cancel flow themselves,
rather than it happening for them.

**Etsy can't be told about a StockSmith-initiated cancel — confirmed via Etsy's own API
reference**: Etsy's public API has **no seller-initiated cancel/refund write endpoint at
all**. `updateShopReceipt` only toggles `was_shipped`/`was_paid` booleans; `ShopRefund`
records are read-only. So Etsy cancellation is inherently **one-directional**
(Etsy → StockSmith); a cancellation started *in* StockSmith can never be pushed back to
Etsy. The seller still has to action the actual cancel/refund on Etsy's own seller
dashboard — StockSmith can only ever reflect that decision locally. Worth stating
explicitly so expectations are set correctly, since this isn't a StockSmith gap to close,
it's a platform constraint.

**Should the same apply to eBay? Does a cancellation on one platform correct the other's
inventory?**
- Recommend applying the identical **pull-only, prompt-don't-auto-apply** behavior to
  eBay once its order sync is live, for one consistent mental model across platforms —
  even though eBay's Post-Order/Fulfillment APIs *do* expose seller-side
  cancellation/refund write endpoints (`createCancellation`, `issue_refund`) unlike Etsy.
  Whether to ever use those to push a StockSmith-initiated cancellation *to* eBay is a
  real, separate design decision worth deferring to when eBay's sync actually exists, not
  deciding blind now.
- Cross-platform inventory correction: **yes, automatically** — since Section 3 already
  establishes `current_stock`/`allocated_qty` as the single shared source of truth, a
  cancellation's scrap/return effect on those fields is picked up by the very next
  quantity push (Section 1d/2) to *every* platform with a `Listing`, with no separate
  "notify the other platform" logic needed. This is a confirmation of the existing design,
  not a new mechanism.

---

## Data model changes (consolidated)

- **New** `order_line_returns` — `order_line_id` FK, `qty`, `disposition`
  (scrap/return_to_stock), `source` (cancel_before_ship/return_after_ship), `reason`,
  `created_at`. The scrap/return audit trail.
- **New** `PlatformAppCredential`-style table (or equivalent) — `platform`,
  `environment` (sandbox/production — eBay only needs this dimension), `client_id`,
  `client_secret` (EncryptedString), `public_base_url` override. Install-level app
  settings, separate from `PlatformConnection`'s per-OAuth-grant state.
- **Extend** `PlatformConnection` — `sync_interval_minutes` (nullable int, per-platform
  override of the current global `etsy_poll_interval_minutes` default), same editable
  pattern as the existing `sync_start_date`.
- **Extend** `Order` — a structured `pending_marketplace_cancellation`-style flag (or a
  documented extension of the existing `sync_issue` convention) so "needs a decision" is
  distinguishable from today's informational sync_issue banner.
- **No change** (deliberately) — no shipping-address field is added to `ExternalOrder`/
  `Order` for either platform; this is the data-minimization decision behind Sections 1e/2
  (skipping eBay's Marketplace Account Deletion endpoint requirement) rather than a gap to
  fill in later.
- **Behavior change, no schema change** — `order_sync._upsert_order` stops writing
  `ext_order.buyer_name`/`buyer_note` onto `Order.buyer_name`/`buyer_note` for synced
  orders (both platforms). The columns themselves stay, since manually-created orders
  still use them for the user's own notes.
- **New** small "listing push outcome" log — either a `push` variant of
  `PlatformSyncRun.mode`, or its own `platform_listing_pushes` table (product_id,
  variant_id, platform, attempted_qty, status, error_message, attempted_at) — needed to
  answer "did the last quantity push fail," per the user's explicit ask about failure
  visibility.
- **Extend** `PlatformStatus` (schema, not DB) — `last_sync_attempt_at`/
  `last_sync_success_at`/`last_sync_error`, derived from `PlatformSyncRun`, so a failing
  auto-sync attempt is visible without opening the sync log.

---

## API endpoints / scopes needed

**Etsy** (currently `listings_r`, `transactions_r`):
- Add **`listings_w`** — `PUT /v3/application/listings/{listing_id}/inventory`
  (`updateListingInventory`) for quantity push. Existing connections need to reconnect
  once this is added (scopes are fixed at grant time).
- No cancellation/refund write endpoint exists on Etsy's API — confirmed nothing to add
  here; see Section 4.

**eBay** (currently `sell.fulfillment.readonly`, `sell.finances`,
`sell.inventory.readonly`, **`commerce.identity.readonly`**):
- The identity scope was missing in the first pass and confirmed missing via a live
  Sandbox connect attempt (`403 Insufficient permissions`) — `fetch_account_id` calls the
  Identity API (`commerce/identity/v1/user/`), which none of the Sell-API scopes above
  cover. Added and reconnect required for any connection made before this fix.
- Add **`sell.inventory`** (write) for quantity push — `PUT
  /sell/inventory/v1/inventory_item/{sku}` or the per-offer quantity update, mirroring
  Etsy's `listings_w` addition.
- **Marketplace Account Deletion**: no scope, no endpoint, no subscription — default plan
  is to self-certify the exemption in the dev portal (Section 2), contingent on never
  persisting eBay buyer name/username/address anywhere (Section 1e). Only needed if that
  changes later.
- Deferred, not needed now: `sell.fulfillment`/Post-Order write scopes, only if a later
  decision is made to push cancellations to eBay (Section 4).

---

## Edge cases / failure modes worth deciding now

- **Auto-sync vs. a user mid-review of a preview**: no real conflict — `preview_sync`
  never writes, so nothing to protect; just don't auto-refresh the preview view out from
  under the user while a modal is open.
- **App closed for days, reopened**: no special handling needed — the existing
  `sync_start_date`/`last_orders_synced_at` watermark logic already handles "catch up from
  wherever we left off" correctly as-is.
- **Token revoked mid-cycle**: auto-disable the scheduler for that platform after N
  consecutive `PlatformAuthError`s rather than retrying a dead connection every interval;
  surface a persistent reconnect banner.
- **Push racing a fresh manual adjustment**: solved by recomputing the quantity at *send*
  time inside the debounce window, not at enqueue time (Section 1d) — make sure the
  implementation actually does this, it's an easy place to get it backwards.
- **Packaging runs out with no order attached to notice**: already covered — a
  packaging-material shortage lowers `compute_max_sellable`'s output directly, which then
  flows into the next push automatically. No separate handling needed.
- **Product with no Listing on any platform**: push is a no-op — already true of
  `sync_listing_ceiling_qty`'s existing guard; keep that behavior.

---

## Open questions for you

1. **Confirm the root-cause diagnosis** (Section 0) once a credentials field exists:
   does entering the same Etsy Client ID/Secret from `backend/.env` make `preview-sync`
   work immediately, with no reconnect? This validates the fix before more work builds on
   top of it.
2. **Where should platform credentials live**: DB-stored + encrypted + editable in
   Settings (recommended — this app has no per-install secret-injection pipeline), or some
   other mechanism you have in mind?
3. **OAuth redirect for Etsy**: try registering a loopback `http://127.0.0.1:8000/...`
   redirect URI directly first (no new infra), or go straight to a hosted relay? I'd try
   loopback first.
4. **Confirm the no-buyer-PII default is acceptable long-term** (Sections 1e/2): no
   in-app "who is this order for" on synced orders, no in-app eBay shipping labels, ever
   (without revisiting this and building the deletion-notification endpoint after all).
   If in-app fulfillment/labels is something you want on the roadmap, better to know now
   than after the exemption's already been claimed.
5. **Auto-sync interval**: keep the existing coded default of 15 minutes for both
   platforms, independently configurable per platform? Any preference?
6. **Scrap/return defaults**: agree with "packaging → default scrap, the finished
   product unit → default return to stock" for the shipped-and-returned case? Or would you
   rather every post-ship return line default to scrap uniformly (simpler mental model,
   less accurate for a genuinely resellable returned item)?
7. **Push-failure visibility**: a passive badge on the product (as proposed), or
   something more assertive (toast/notification), given a stale marketplace quantity is a
   real oversell risk, not just cosmetic staleness?
8. **Cancellation push-back to eBay**: once eBay's sync exists, should StockSmith ever
   use eBay's cancellation/refund write endpoints to push a StockSmith-initiated cancel
   out, or stay pull-only like Etsy for one consistent model across both platforms
   (recommended for now)?

---

## Suggested build order

1. ✅ **Unblock the packaged app** (Section 0/1a) — DB-stored, in-app-editable Etsy/eBay
   credentials + resolved redirect strategy. Confirmed live: entering credentials fixed
   the root-cause 400.
2. ✅ **Stop persisting marketplace buyer identity** (1e) — `order_sync._upsert_order`
   no longer writes `buyer_name`/`buyer_note` for synced orders (either platform).
3. ✅ **Etsy: sync-status correctness + auto-sync scheduler** (1b/1c) — background
   per-platform scheduler (turned out platform-generic, not Etsy-only, so eBay auto-sync
   already works once connected), shared lock with manual sync, auto-disable after 3
   consecutive auth failures, `last_sync_attempt_at`/`last_sync_success_at`/
   `last_sync_error` surfaced.
4. ✅ **Etsy: quantity push** (1d) — `listings_w` scope, real `push_listing_quantity`
   (GET-then-PUT full inventory replace), debounced trigger wiring across allocation/
   builds/stock adjustments/material consumption/`platform_ceiling_qty`, failure log +
   UI badge. Verified with a mocked adapter (real Etsy listings exist in this shop; no
   live call was made without the user's explicit go-ahead).
5. ✅ **eBay Sandbox verification** — connected a real Sandbox test account and ran
   `preview-sync`/`check-all-listings` against it. Found and fixed two real bugs in the
   process, not just parsing nits:
   - Missing `commerce.identity.readonly` scope — `fetch_account_id`'s Identity API call
     403'd without it; none of the Sell-API scopes cover it.
   - SQLite doesn't reliably round-trip timezone-aware datetimes through SQLAlchemy — a
     freshly-ORM-written `access_token_expires_at`/`last_orders_synced_at` came back
     naive on the next read, crashing every comparison against `datetime.now(timezone.
     utc)`. Fixed with a shared `ensure_utc()` helper (`platforms/base.py`) applied at
     every affected comparison site — token-refresh checks in both adapters, and
     `order_sync._effective_since`'s sync-watermark comparison. This one would eventually
     have hit Etsy too (any connection whose watermark gets rewritten through the ORM,
     which is all of them), not just eBay — it just hadn't been triggered yet.
   - Also required standing up a small Cloudflare Worker relay for eBay's OAuth redirect,
     since eBay's redirect-URL registration flatly requires `https://` — confirmed
     directly from the dev portal's own form validation, not inferred.
   - Not yet exercised: real (non-empty) order/listing parsing, since the connected
     Sandbox account has no orders or listings on it. Revisit if that becomes available.
6. ✅ **Declare the eBay Marketplace Account Deletion exemption** in the dev portal —
   declared and approved.
7. ✅ **eBay quantity push** — `EbayAdapter.push_listing_quantity` implemented via Sell
   Inventory API's `bulkUpdatePriceQuantity` (a targeted partial update, unlike Etsy's
   full-replace — no GET-then-PUT needed), `sell.inventory` write scope added. Verified
   request-building and response-parsing (success, per-item error, missing-SKU, no-SKU
   cases) with a mocked adapter — no real listing exists yet to push against for real.
8. ✅ **Cross-platform push wiring** (Section 3) — turned out to be a one-line change
   (`listing_push._PUSH_ENABLED_PLATFORMS` gains `ListingPlatform.ebay`) since `_push_now`
   already queried `Listing` rows by platform generically. Verified live against a real
   product with both an Etsy and an eBay `Listing` row: correctly pushed to the one with
   a real `external_listing_id` (Etsy) and correctly skipped the other (no eBay listing
   exists yet).
9. ✅ **eBay Production cutover** — connected against the real Production keyset
   (`hinetts_home`), reusing the same Sandbox/Production infrastructure from Step 5 (no
   new code needed — environment-switching was already generic). Deliberately left
   `auto_sync_enabled` off and no listings checked yet, so nothing has pulled real orders
   or pushed to real listings — connected-but-idle until manually turned on.
10. ✅ **Cancellation & returns workflow** (Section 4) — `order_line_returns` model
    (per-line, per-scope scrap/return-to-stock audit trail), `services/returns.py`
    (cancellation preview + processing, superseding `allocation.cancel_order`), new
    `GET .../cancellation-preview` + reworked `POST .../cancel` endpoints, and a real
    dialog UI defaulting return-to-stock for the product and scrap for kitting packaging
    (both overridable). `order_sync._reconcile_status` no longer auto-applies an
    Etsy-reported cancellation — it sets `Order.pending_marketplace_cancellation` and
    surfaces a review banner instead, on the order detail page and in the orders list.
    Verified against real order data using transaction rollback throughout (no real order
    was actually mutated by testing) plus one disposable manual test order, created and
    deleted. Caught and fixed a real bug in the process: an unconditional
    `reconcile_order_kitting` call after cancelling would double-charge a shipped line's
    packaging material on any order whose kitting ledger had drifted from reality (which,
    on inspection, some real seeded/migrated orders in this dataset had) — removed as
    both redundant (`allocation.deallocate_line` already reconciles when there's actually
    something to reconcile) and unsafe.
