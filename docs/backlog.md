# Backlog

Informal list of improvements not yet scheduled into a plan doc.

**Upkeep:** at release prep, cross-reference every entry against the release branch and
delete the ones that release completed (or rescope the partly-done ones). A stale backlog
costs a whole review pass re-discovering what already shipped — which is what happened by
0.10.0.

Grouped by feature area; within each group, roughly most-valuable first.

---

## Platform sync reliability

### Disconnecting a platform silently switches auto-sync off

**Problem:** `disconnect` (`backend/app/routers/platforms.py:643`) sets `auto_sync_enabled = False` along with clearing the tokens. Reconnecting doesn't restore it — the flag defaults to off for a freshly-connected shop, deliberately, so a new connection can't start unattended commits before the user has run a manual sync. The consequence is that a disconnect/reconnect cycle on an *established* connection, which is the first thing anyone tries when a platform looks stuck, quietly turns off the very thing they're trying to fix.

Nothing surfaces it. `_tick` returns early on the flag (`backend/app/services/sync_scheduler.py`), so there are no sync runs, no errors, and no log lines — while the settings panel goes on showing the platform as connected. The only visible difference between "auto-sync is off" and "the shop has had no new orders" is the absence of rows in a table nobody has reason to open.

Confirmed live: a shop reconnected Etsy on 14 Aug and had no sync attempt of any kind until auto-sync was manually switched back on two days later. eBay, untouched, kept syncing every 15 minutes throughout — which is what made it look like an Etsy fault.

**Ask:** Decide between two shapes and implement one. Either preserve `auto_sync_enabled` across a reconnect of a connection that already had it on (the safety argument for defaulting off applies to a *first* connection, not to re-authorising one that has been syncing for months), or keep clearing it and make the off state loud — the reconnect flow says it's now off, and the panel distinguishes "auto-sync off" from "auto-sync on, nothing to do" rather than rendering both as silence. The second is the smaller change; the first is what stops the reconnect-as-first-aid reflex from making things worse.

### Crash-recovery watchdog for the tray

**Problem:** The tray, autostart, PID file and sidecar supervisor all shipped (0.10.x tray branch). One piece of `docs/plan-background-sync.md` §6c is still unbuilt: `lib.rs` writes a `backend.stopped` marker on a deliberate Quit and clears it on launch, but nothing reads it. So an app killed by a *crash* (rather than quit on purpose) doesn't come back until the user launches it by hand — which for an unattended sync machine can be days.

**Ask:** The §6c watchdog — a lightweight external check (Scheduled Task or a second tiny process) that relaunches StockSmith `--hidden` when it finds the shell gone *and* no `backend.stopped` marker present. Small, but it's the difference between "restarts a crash" and "restarts something the user closed on purpose".

---

## Stock-take workflow

### Row-level abandon action on the stock-takes list (optional follow-up)

**Problem:** The two substantive fixes here shipped: the slide-over "Abandon stock take" action (detail footer, open takes only), and a create-time 409 when an open take already renders the identical `scope_description` — which stops the start-flow double-fire (seen 2026-09-02, takes #3/#4 six seconds apart) from producing an orphan. `create_stock_take` still *warns* on a partial overlap and proceeds; only an exact-scope match is blocked.

**Ask:** Nice-to-have only — an abandon action on the list rows (`routes/stock-takes/route.tsx`) so an open take can be dropped without opening it. Also consider surfacing the exact-scope clash in the scope picker's preview panel (alongside the existing overlap warning) so the user sees it before clicking Start, rather than as an error afterward.

### An async session is unusable after a rolled-back flush error

**Problem:** Found while building the stock-count work. In this dependency set (SQLAlchemy 2.0.51, aiosqlite 0.22.1, greenlet 3.5.3) there is a reproducible condition where an `AsyncSession` becomes permanently unusable once a flush raises `IntegrityError` and the session is rolled back. Every later statement on it — including a bare column `SELECT` that touches no ORM object — raises `MissingGreenlet: greenlet_spawn has not been called`. An explicit `await session.rollback()` by the caller does not recover it; a **new** session on the same engine is fine.

Reproduces on the app's own code via `costing.create_adjustment`'s "would make current_qty negative" path, on both the test harness's StaticPool and a production-shaped engine, and also with no app code at all:

```python
m = Material(name="R", category=..., unit=...)
s.add(m); await s.commit()
m.current_qty = Decimal(-5)          # violates ck_materials_current_qty_nonneg
try: await s.flush()
except IntegrityError: await s.rollback()
await s.scalar(select(Material.name))  # MissingGreenlet
```

The trigger is narrow and not fully isolated: a UNIQUE violation on a session that has not previously committed recovers cleanly, and so does a CHECK violation on an object loaded fresh into its own session. It appears to need a prior commit on the same session. Worth pinning down before assuming any particular fix.

**Impact today is nil**, which is why nothing has caught it: every request gets its own session and ends at the error, so nothing reuses a poisoned one. `csv_io.py` is the one place that catches per row and continues, and its failures are pre-flush validation errors (`validate_qty_for_unit`) rather than flush errors, so it stays on the good path — but a row that did violate a constraint would take the rest of the import down with a 500 rather than landing in the `failed` list, which is the shape of the bug this becomes.

**It does block a planned design.** The stock-take approve loop (`docs/plan-stock-take.md`, Phase B) was specified to catch a refused adjustment, mark that line for manual review, and carry on. It cannot do that on a shared session. Phase B works around it two ways — pre-checking allocation and movement in Python so a refusal is rare, and giving each line its own session — but the underlying fault stays.

**Ask:** Isolate the exact trigger and decide whether it's an aiosqlite 0.22 regression (0.22 is recent and changed the connection/threading model) or a SQLAlchemy dialect incompatibility, then pin or bump accordingly. A dependency change wants its own commit and its own green run, not to ride along inside a feature.

### Settle a flagged stock-take line inline on the count sheet

**Problem:** Surfaced via the design canvas — it settles a held-back line (Accept / Keep / Recount) in a `Settle` column right in the count-sheet row. The real app removed the in-take Review tab and routes every flagged line to the standalone `/stock-takes/unresolved` page instead. That page works and is the right home for lines from *closed* takes, but settling one you just approved means leaving the take.

**Ask:** When a take is closed but still has `conflict` lines, render the Accept / Keep / Recount actions inline in the count-sheet row (reusing `stockTakesApi.resolveLine`), so a just-approved take can be cleared without a page change. The Unresolved page stays as the cross-take view.

### Variance value column on the stock-take count sheet

**Problem:** Surfaced via the design canvas — its stock-take sheet has a `Value` column showing the money impact of each line's variance (`delta × unit cost`). The real sheet (`src/routes/stock-takes/$stockTakeId.tsx`) shows Expected / Counted / Delta but no monetary figure, and `StockTakeLineRead` carries no unit cost to compute one from.

**Ask:** Put a `unit_cost` (or a precomputed `delta_value`) on the `StockTakeLineRead` schema — materials from `avg_unit_cost`, products/variants from `cost_per_unit` — and add a right-aligned `Value` column. Lets someone triage a sheet by "what's the £ exposure" rather than raw quantity.

---

## Catalogue tidy-up

### Merge two products

**Problem:** Attribute values, variants and materials can now be renamed and merged (0.20.0). The remaining duplicate shape is two *products* that should have been one product with a variant axis — "Pencil Pot Red" and "Pencil Pot Blue" created before variants existed. Considered alongside the other merges and deferred: it is effectively "convert product B into a variant of A", which means giving A an attribute, generating a variant from B's identity, and then running the variant merge against a variant that doesn't exist yet. The listing side is harder still — each product has its own marketplace listing, and folding one into the other's variation set is a listing rewrite, not a stock push.

**Ask:** Only worth doing if it comes up in practice. If it does, build it as "convert to variant of…" on the product page, reusing `services/variant_merge` for the stock/order/SKU-alias half and refusing while B has a live listing.

### Split an attribute value

**Problem:** The inverse of a value merge — "4 Stud" turns out to hide two sizes. Deferred with product merge: generating the new value's variants is already possible, and moving stock between them is a pair of adjustments, so there is a manual path and no one has asked.

## Variant BOM correctness

### Audit existing substitutions onto un-ruled base lines

**Problem:** Found while planning the variant conflict-detection work. If a variant substitutes base line A onto material M, and M is *itself* a base BOM line that nothing substitutes away, no unique-constraint violation occurs (there's no override row for M to collide with) — but `_RESOLVED_VARIANT_BOM_SQL` (`backend/app/services/buildability.py:76-98`) then emits M twice via `UNION ALL`, and `compute_variant_buildability` takes `min()` of per-line bottlenecks against the same `current_qty` rather than summing consumption (`:304-312`). With `current_qty=10` and both lines at qty 1, it reports 10 buildable when the true answer is 5. (`cost_per_unit` sums, so cost is unaffected.)

Phase 4 of the backlog-burndown plan rejects this configuration at generation time going forward, but says nothing about rows already in the database.

**Ask:** Audit existing `product_variant_materials` for substitutions targeting an un-ruled base BOM line, and decide whether `_RESOLVED_VARIANT_BOM_SQL` should sum duplicate materials rather than emit them twice — which would fix any such rows already stored, rather than only preventing new ones.

### Provenance column for overrides

**Problem:** `ProductVariantMaterial` has no column distinguishing a rule-generated override from a hand-edited one. The bulk-amend feature therefore can't preserve manual edits automatically — it has to show a preview and make the human consent to each overwrite.

**Ask:** Add a `source` column (`"rule" | "manual"`) so bulk operations can leave hand-edited rows alone by default.

---

## Purchasing & reorder intelligence

### Material cost-trend indicator

**Problem:** Surfaced via the design canvas, which shows a read-only "+1.7% since June" price-change stat on a material's Purchasing tab. Today the only record of cost history is raw stock-movement rows — there's no derived trend figure.

**Ask:** A calculated cost-change-over-window figure (e.g. current unit cost vs. unit cost N months ago), derived from existing purchase/receiving history. Needs a new backend calc, not new storage.

---

## Pricing & margins

### Per-order-line pricing guardrails

**Problem:** Surfaced via the design canvas, whose "Priced per order line" mode adds a "Minimum accepted price" floor and a "Warn below margin %" threshold for custom-priced lines. Today a manually-priced order line has no floor or margin warning — nothing stops a line being priced below cost by mistake.

**Ask:** New optional fields on the order-line pricing flow: a minimum-price floor (reject/confirm below it) and a margin-warning threshold (soft warning, not a block). Needs a small backend validation addition; no new storage beyond the two settings values.

### Per-product channel-fee override fields

**Problem:** The side-by-side Etsy vs eBay fee/margin comparison itself is built — `ChannelFeeComparison` in `frontend/src/components/products/PricingSection.tsx` shows fee %, fee £ and resulting margin per channel at the product's own price. What's not built is the per-product *override*: the canvas's round-5 Pricing tab has editable Etsy%/eBay% fee fields so a product with a negotiated or atypical fee rate can diverge from the global fee config.

**Ask:** Optional per-product fee-rate overrides (percent, per channel) that `ChannelFeeComparison` and the margin calc use in place of the global `platform_fee_components` rate when set. New nullable fields on the product; the comparison component already has the shape to display them.

---

## Replacement parcels & postage labels

### eBay bulk label purchases have no per-order cost

**Problem:** Seller Hub's bulk "buy labels" flow books ONE `SHIPPING_LABEL` transaction for the whole batch (batch total, no `orderId`, `buyer.username: "EBAY"`), and getTransactions returns it for the orderId filter of every order in the batch. Confirmed live on 09-15158-06992 (six £3.65 labels, one £21.90 transaction, only one of the six orders got any label at all). `EbayAdapter._parse_shipping_labels` now stores such a label with `amount NULL` so profit keeps the profile estimate, but the true per-order figure — which Seller Hub's own transaction page does itemise — is never captured, and the *other* five orders in the batch never see a label recorded (`order_postage_charges` is unique on `(platform, external_id)`, so `apply_postage_charges` logs and skips the label for every order after the first one synced — since 0.18.1; before that the second order failed the whole sync). They fall back to the estimate too, which happens to be the same number here.

**Ask:** Find where eBay exposes the per-label breakdown (Sell Logistics `getShipment` by the fulfilment's tracking number is the likeliest candidate; the Seller Hub page is not an API) and fill the amount in from there. Also decide whether the other orders in a batch should get a NULL-amount label recorded so a later single label on them is treated as a resend rather than the original.

### Voided / refunded shipping labels aren't netted off

**Problem:** eBay reports a label refund as a `SHIPPING_LABEL` transaction with `bookingEntry: CREDIT`; Etsy posts a positive-amount ledger entry. Both are currently logged and skipped (`EbayAdapter._parse_shipping_labels`, `EtsyAdapter._extract_postage_charges`), so a label that was bought, voided and re-bought counts twice against the order's postage until someone deletes the spurious replacement parcel by hand. Which of several labels a credit reverses isn't stated by either marketplace, so it can't simply be matched by amount.

**Ask:** Store credits as negative `order_postage_charges` rows (or a `voided_at` on the charge they most plausibly reverse — same amount, later date, no parcel linked yet) and exclude voided labels from `sequence` numbering so a re-bought original label doesn't spawn a replacement parcel.

### Etsy ledger label shape is unconfirmed

**Problem:** Etsy's `PaymentAccountLedgerEntry` documents `ledger_type`/`reference_type` only as free text, so label detection (`EtsyAdapter._extract_postage_charges`) is a heuristic — a marker word in the type/description plus a reference back to the receipt or one of its transactions (an entry that only references the label's own id is deliberately ignored since 0.18.1 — the 30-day crawl window would hand it to every receipt shipped in that window). It has not yet been checked against a real Etsy shop's ledger. Misses are the expected failure mode (the profile estimate then stands); the diagnostic INFO line "ledger entries not classified as fee, VAT or shipping label" lists every unrecognised entry that references a shipped receipt.

**Ask:** Run `scripts/backfill_postage_charges.py --platform etsy` (dry run) against the live shop, read the diagnostic lines, and tighten `_LABEL_MARKERS` / the reference rules to the real values. The 30-day ledger window cap in `_fetch_platform_fees_total` also means a replacement sent more than a month after the receipt is never seen — widen it for the label crawl if that turns out to happen.

### Editing a parcel's items

**Problem:** Items on a recorded replacement parcel can't be changed — the UI says delete and re-record. That keeps `services/order_parcels` to one forward and one reverse stock path, but a typo in a quantity currently costs the user the whole parcel (including its tracking number and notes).

**Ask:** A diff-based item edit that restocks removed/reduced items and deducts added/increased ones in one transaction, reusing `_consume_product`/`_consume_material` and their reversals.

---

## eBay integration hardening

### eBay refunds are recognised but never recorded

**Problem:** `EbayAdapter._parse_order` never sets `refunded_amount`. The adapter plainly
knows a refund happened — `_ORDER_PAYMENT_STATES` maps `PARTIALLY_REFUNDED` to settled and
`FULLY_REFUNDED` to reversed — it just never reads the amount, so the column stays NULL on
every eBay order. Etsy fills it from `_sum_refunds(receipt.refunds)`.

`_compute_net_profit` subtracts `Decimal(order.refunded_amount or 0)`, so a partially
refunded eBay order counts money that went back to the buyer as profit, and the Refunded
figure never appears on the order page. Same shape as the discount bug fixed in 0.7.2, and
found alongside it: a field the adapter simply does not read, failing silently because a
NULL is indistinguishable from an order that was never refunded.

Not fixed with the discount because it has a different cause and a different source — the
refund total is not in `pricingSummary` at all, so it needs a decision about where to read
it from (the Sell Finances REFUND transactions, which `_fetch_transactions` already fetches
and filters to SALE, look like the closest fit).

**Ask:** Populate `refunded_amount` for eBay, and extend the reconciliation warning added
in 0.7.2 to account for refunds so a mismatch keeps being noisy. Worth checking against a
real partially-refunded order before trusting the field — the SALE-only filter in
`_fetch_transactions` is the reason nobody noticed the REFUND rows were there.

### Turn the Offer-enrichment tests into an opt-in sandbox integration test

**Problem:** The Offer-enrichment work (`EbayAdapter._enrich_with_offers` and friends) is covered by tests, but every one of them serves canned responses through a fake HTTP client (`_RoutedFakeClient`, `test_ebay_offer_enrichment.py`). No request has ever gone to a real eBay account, sandbox or production. The response shapes it parses — `offer.listing.listingStatus`, `offer.status`, `product.aspects` — come from eBay's documentation rather than from observed payloads, which is exactly the class of assumption that put "eBay's token response contains a `scope` field" (it doesn't) into a release.

The sandbox plumbing already exists: `_HOSTS[PlatformEnvironment.sandbox]` has the full host map and the adapter takes an `environment` arg. What's missing is a test that actually uses it.

**Ask:** Add an integration test that hits `api.sandbox.ebay.com`, `@pytest.mark.skipif` unless `EBAY_SANDBOX_CLIENT_ID` / `EBAY_SANDBOX_CLIENT_SECRET` / `EBAY_SANDBOX_USER_TOKEN` are in the environment, so the default `pytest` run and CI-without-secrets stay hermetic. It should confirm against a sandbox account seeded with the four scenarios: variation strings render in the same format as Etsy's, listing state maps correctly for active / out-of-stock / ended, a SKU with no offer reports `no_offer`, and the fan-out stays within its concurrency cap. Fold any shape corrections back into the fake-client fixtures in `test_ebay_offer_enrichment.py` so the hermetic tests reflect reality. This backstops the unit tests; it does not replace them (eBay sandbox is flaky and rate-limited).

### Use the real listing id as external_listing_id

**Problem:** `EbayAdapter._index_inventory_item` sets `external_listing_id=sku` (`backend/app/services/platforms/ebay.py:1197`), which isn't a listing id. The real one is now fetched during offer enrichment (`_enrich_with_offers`) but deliberately not written (`:1168`), because for eBay that field holding the SKU is a documented invariant with a second writer: `listing_adoption.apply_adoption` mirrors it, and `docs/listing-adoption.md` states it as a safety property.

Functionally nothing reads it except null checks, and the frontend types but never renders it. One behaviour would improve: `listing_push._get_listing_lock` would key on the real listing id, so variants of one multi-variation listing serialise instead of racing.

**Ask:** Switch `external_listing_id` to the real eBay listing id, updating `listing_adoption.apply_adoption`, `docs/listing-adoption.md` and the ~8 test assertions that encode the current invariant in lockstep. Worth doing as its own commit so it stays revertible.

---

## Performance

### Thumbnail pop-in and slide-over cold-open latency

**Problem:** Two related symptoms on the list screens.

*Thumbnail pop-in (Materials, Products lists).* Each row thumbnail is a JS-driven pipeline: `useLazyVisible` (IntersectionObserver, 200px rootMargin) gates `useMaterialImageUrl`, which calls `materialImageThumbnailUrl(id)` → `authHeaders()` + `baseUrl()` (each a fresh `LazyStore` + two `store.get` reads — `getSettings` is not memoised), then `platformFetch` (dynamic `import("@tauri-apps/plugin-http")` the first time), then `response.blob()`, then `URL.createObjectURL`. The result is cached only in a module-level `Map`, so a full app reload re-runs the whole pipeline for every visible row. The endpoint (`GET /materials/{id}/image/thumbnail`, `routers/materials.py:389`) returns a bare `FileResponse` with no `Cache-Control` / `ETag`. Net effect: thumbnails arrive one blob at a time, after scroll, and never survive a reload — visible pop-in.

*Slide-over feels slow to open.* `routes/materials/$materialId.tsx` fires six queries on mount — `["materials", id]` (blocks the panel body), `stock-history`, and four reference lists (`manufacturers`, `suppliers`, `material-types`, `colours`) for the edit dropdowns. None set `staleTime`, so the reference lists refetch on every open; `["materials", id]` has no `placeholderData` / `initialData` seeded from the list row that's already in memory, so the panel can't paint until the round-trip returns. The product slide-over (`$productId.tsx`) has the same shape.

**Ask:** A pass with a few independent wins, roughly in value order:
1. **Memoise `getSettings()`** (module-level promise cache, invalidated on settings write) so building an image URL isn't two store reads each.
2. **Seed the detail query from the list.** `placeholderData: () => queryClient.getQueryData(["materials"])?.find(...)` on `["materials", id]` in both slide-overs, so the panel paints from data already held and then reconciles.
3. **`staleTime` on the reference lists** (`manufacturers`/`suppliers`/`material-types`/`colours`, ~5 min) or prefetch them once at app start — they change rarely and are shared across every detail panel.
4. **Cache headers on the thumbnail/asset endpoints** (`Cache-Control: private, max-age=…` + `ETag`/`Last-Modified`) so even the fetch path revalidates cheaply.
5. **Persist thumbnails across reloads** — either a Cache Storage / IndexedDB layer under `useMaterialImageUrl`, or serve the on-disk `thumb-main.jpg` through Tauri's `asset:` protocol (`convertFileSrc`) so rows can use a plain `<img loading="lazy" decoding="async">` with the webview's own HTTP cache and no auth round-trip. The `asset:` route is the bigger change but removes the JS pipeline entirely.
6. **Prefetch the detail query on row hover / `mousedown`** so the click-to-open feels instant.

Worth measuring first: run the dev build with the browser tools, capture per-route load timings and the thumbnail request waterfall, and confirm which of the above actually move the number before doing all six.

---

## Schema debt

### Drop the superseded `materials.colour` and `materials.category` columns

**Problem:** Two reference-table migrations deliberately left their old column in place for a release rather than dropping it, because SQLite needs a full table rebuild to drop a column and restoring an older backup and migrating it forward is routine. `materials.colour` was left by `e6b21d84f309` (0.6.x) and is now several releases overdue; `materials.category` was left by `f2a91c4d7b08` and is due next release.

Category is the one with a visible cost while it stays. The column is NOT NULL with a CHECK accepting exactly the original seven values, so a material filed under a user-created category has to store `'other'` there (`services/material_categories.legacy_value_for`). On the current release nothing reads it, so nothing is wrong — but a rollback, or a backup restored into an older build, shows those materials as "other".

**Ask:** One migration per column: drop the column and its CHECK, drop the now-unused `LegacyMaterialCategory` enum, make `materials.category_id` NOT NULL, and delete `legacy_value_for` along with the calls that keep the column in step (materials create/patch, CSV import, and the rename/merge wrappers in `routers/material_categories.py`). The category-name fallbacks in `Material.category_name` / `Material.colour_name` go at the same time, as does the legacy branch of `services/stock_takes._material_category_sort`, which reads the enum's declaration order to place a material whose category row is missing.

---

## List & detail UX (from the design canvas)

### Configurable/hideable list columns

**Problem:** Surfaced via the design canvas, which puts a "Columns" button on every list screen's toolbar (Products, Materials, Orders, Purchases). No real list view currently lets a user show/hide or reorder columns — the column set is fixed per screen.

**Ask:** A shared column-visibility control for list views, persisted per user/screen. Frontend-only, but touches every list route — medium scope, worth doing once as a shared component rather than per-screen.

### Keyboard row navigation (j/k) on list views

**Problem:** Surfaced via the design canvas, which wires `j`/`k` to move a selection cursor up/down the Products and Materials list rows (with Enter to open, X to select). No list view in the real app supports keyboard row navigation today.

**Ask:** Frontend-only — add row-cursor state and key handlers to the Products/Materials list routes, matching the pattern already used for bulk-select. Small scope, but worth doing once as a shared hook if it's wanted on more than one list.

### Dashboard KPI date-range toggle

**Problem:** Surfaced via the design canvas, which shows a This week / Month / Quarter toggle above the dashboard KPI cards. The real dashboard summary is fixed to "this week" with no way to see the same KPIs over a longer window.

**Ask:** Extend the dashboard summary endpoint to accept a range parameter and wire a toggle to it. Small backend query change; frontend is mostly state plumbing.

---

## Colour & settings polish

### Let a new colour's hex be set at the point of use, or link to the colour settings page

**Problem:** When you set a colour on a material (Details tab "Colour" field, `CreatableSelect` backed by `coloursApi`), creating a new colour only records its name — `find_or_create` never sets `hex_code`. So the only way to give a colour a hex (which drives the list/detail chip) is to go to Settings → Reference data → Colours afterwards and edit the row. Nothing on the material form tells you that, or that the colour you just picked has no hex.

**Ask:** On the material colour field: when the user is creating a *new* colour, also offer an optional hex input and pass it through (needs `find-or-create` / the material create/update path to accept `hex_code`, or a follow-up `PATCH /colours/{id}`). When an *existing* colour with no `hex_code` is selected, show a small inline "no colour chip set — add one" link to `/settings?page=lists` (the Colours table). Keeps the reference table as the source of truth while removing the dead end.

---

## Settings redesign follow-ups (from the design canvas)

The Settings page adopted the design canvas's grouped two-level nav (Selling / Stock / App)
in place of the old six flat tabs, relocating every existing field with no functional loss.
The mockup also proposed several genuinely new features; none of these were built, since
they'd need real engine work beyond a settings-screen relocation. Listed roughly in the
order the mockup implied they matter.

### Background push scheduler and a pause-all kill switch

**Problem:** The design canvas's "Stores & sync" page shows a "Background sync" card —
configurable push interval (5/15/30 min, hourly, manual), "push buildable as sellable",
"auto-relist when back in stock", "deduct stock on order vs. dispatch", and a global
"pause all pushes" toggle. None of this exists: today's push behaviour (what triggers a
quantity push, and when) is fixed in code, not configurable.

**Ask:** A real push-eligibility/scheduling engine with these as actual settings, not just
UI. Sizeable — touches the push pipeline directly, not just Settings.

### Sync guardrails beyond the existing listing-limits system

**Problem:** The mockup's "Sync limits" card shows four override-able rows: max quantity
pushed per listing, "refuse push if change exceeds ±N units", minimum interval between
pushes to the same listing, and an on-repeated-failure policy ("pause that listing and
notify"). The existing `PlatformFieldLimit`/`LimitField` system (`platform_limits.py`,
surfaced via `PlatformLimitsEditor`) already covers marketplace *content* limits (SKU
length, title length, `quantity_max`, etc.) with exactly this override UX — "max quantity
pushed" plausibly already maps onto `quantity_max` and should be verified before treating
it as new. The other three rows (delta guard, interval, failure policy) have no backend
equivalent at all.

**Ask:** Confirm the `quantity_max` mapping; for the remaining three, a real push-safety
guardrail engine wired into the actual push pipeline — settings rows with no enforcement
behind them would be actively misleading.

### Listing profile fields: Etsy renewal and eBay handling time

**Problem:** The mockup's "Listing defaults" panel shows four fields; two already exist on
`ListingProfile` (`etsy_processing_min`/`max`, `ebay_return_policy_id`) and were surfaced in
the redesign. "Etsy renewal" (automatic/manual) and "eBay handling time" (business days) do
not exist as columns anywhere.

**Ask:** Add both as new `ListingProfile` columns (small model + migration change) once
prioritized, sent through at draft-listing time the same way the existing fields are.

### Currency: round prices to a nearest increment

**Problem:** The mockup's Currency card adds a "Round prices to" dropdown (0.01/0.05/
0.50/1.00). Investigated during the redesign: there is no single centralised
currency-formatting call site to hang this off — `lib/money.ts`'s `formatMoney`/
`formatUnitCost` cover some places, but roughly two dozen components still hardcode `£`
literals directly. A rounding setting added only to the centralised helpers would be
inconsistently applied and worse than not having it.

**Ask:** First do a pass centralising money *display* through one formatter (a separate,
smaller piece of work), then add symbol-position and rounding as settings on top of that —
otherwise this ships half-working. Note the "Symbol position" (Before/After) field from the
same mockup card was **not** added for the identical reason, and should be picked up
together with this one.

### Pricing & fees: target margin threshold and extra fee-source modes

**Problem:** The mockup's Margin card adds a "Target margin %" (to flag below-margin
products amber in the product list). It doesn't exist: there's no margin-threshold concept
feeding the product list's colour-coding. Also proposed: extending the margin fee-source
enum (`MarginFeeSource`, currently `manual | etsy | ebay`) with "whichever is cheapest" and
"no channel" options, which need new comparison logic in fee estimation, not just new enum
values.

The mockup's "Include postage in COGS" toggle (off treats postage as buyer-recovered) is
no longer needed: product margin now counts postage charged as revenue and postage cost as
a cost (`pricing.compute_profit_margin`), the same shape as order net profit, so the
buyer-recovered case falls out of the arithmetic rather than needing a switch.

**Ask:** Two separable pieces — (1) a target-margin setting plus product-list amber-flag
wiring, and (2) the two new fee-source comparison modes. Each is real backend logic, not
a settings relocation.

### Forecasting: count usage from sales, builds, or both

**Problem:** The mockup's Forecasting card adds a "Count usage from" dropdown (Sales +
builds / Builds only / Sales only). The forecast calculation (`services/forecasting.py`)
has one fixed usage-rate calculation today with no channel selector.

**Ask:** Extend the forecast calc to compute usage from the selected source(s) before
adding the setting — otherwise it's a dropdown that does nothing.

### Stock counts: default count scope and blind counts

**Problem:** The mockup's "New counts" card adds a "Default count scope" dropdown (by
material category / by product category / everything / only overdue) and a "Blind counts"
toggle (hides the expected quantity from whoever is counting). Neither exists: a new stock
take doesn't currently have a configurable default scope, and the count sheet always shows
the expected figure.

**Ask:** "Default count scope" needs checking against however a new stock take is created
today (`routes/stock-takes`) to see whether a scope concept already exists there that this
setting should feed, rather than inventing a disconnected one. "Blind counts" needs an
actual UI change to the count-sheet screen to hide the expected-quantity column/field when
the setting is on — a real feature, not a settings-page addition.

### Backup: finer schedule granularity and back-up-on-quit

**Problem:** The mockup's Backup card offers a schedule dropdown (Nightly at a fixed hour /
every 6 hours / hourly / manual) and a "Back up on quit" toggle. The current
`BackupSettings` model only supports "daily, at hour N" (`scheduled_enabled` +
`scheduled_hour_local`) — no sub-daily cadence — and there's no app-lifecycle hook wired to
run a backup on quit.

**Ask:** A cadence concept beyond daily-at-hour in `backup_scheduler.py`, and (separately)
verifying Tauri actually exposes a reliable on-quit hook before promising this — the app is
also designed to keep running in the background/tray (see `BackgroundSyncSettings`), which
may make "on quit" a rarer event than the mockup assumes.

### Deep-linking into a specific Settings panel

**Problem:** The mockup's product slide-over has a "Stores" tab with an "Open listing
profiles" link that jumps straight to Settings → Stores & sync → Etsy → the listing-profiles
panel. Since 0.17.0 the Settings route carries the store in the URL
(`?page=stores-sync&store=etsy`) and the sidebar's "Sync problem" link uses it, but there's
still no param for which panel on the store page should be expanded or scrolled to.

**Ask:** Extend `validateSearch` with an optional `panel` param and have the store page
honour it on load (open the right Disclosure row, scroll it into view). Small,
self-contained routing work — worth scoping on its own rather than bundling into a future
settings change.
