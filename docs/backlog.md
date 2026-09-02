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

### Etsy quantity pushes fail permanently when a listing's quantity doesn't vary by variation

**Problem:** Etsy rejects a per-SKU quantity with `400 {"error":"quantity must be consistent across all products"}` when the listing's quantity isn't attached to a variation property. `push_listing_quantity` passes `quantity_on_property` straight back as read (`backend/app/services/platforms/etsy.py:775`), so when it comes back empty on a multi-variation listing, every product in that listing has to share one quantity and there is no per-SKU push that can succeed.

StockSmith treats this like any other push error: a WARNING, a `PlatformListingPush` row with status `error`, and a bump to the badge that `_failing_push_counts` (`backend/app/services/sync_status.py:60`) feeds. But this failure is *structural*, not transient — it will fail identically on every future attempt until the seller changes the listing's variation setup on Etsy, which is not something the app can do or even ask for. The badge therefore accrues a count that no retry will ever clear, and the message the user sees is Etsy's raw 400 body, which doesn't say what is wrong or what to change.

Live on this shop: 7 failing pushes across two listings, both with several variants mapped to one listing id and an identical `external_quantity` on all of them (`listings` rows for products 12 and 15) — the signature of exactly this configuration.

This matters more once the periodic reconciliation sweep below exists: a sweep that re-pushes every errored listing would retry these forever, burning quota on a call that cannot succeed.

**Ask:** Detect the condition rather than discovering it in a 400. The GET that `push_listing_quantity` already performs carries everything needed — an empty `quantity_on_property` alongside more than one non-deleted product means no per-SKU push is possible. Fail fast with a message naming the fix ("this Etsy listing's quantity doesn't vary by variation — enable it on the listing, or the variants can't be stocked independently"), and mark the failure as permanent/structural so it's distinguishable from a transient one: the badge can direct the user to the listing that needs changing, and any future retry sweep can skip it instead of hammering it.

### Periodic reconciliation for failed listing pushes

**Problem:** `listing_push` is entirely event-driven with a 5-second debounce and no retry, so a push that fails permanently (network blip, expired token, marketplace 5xx) is never retried until unrelated stock movement happens to trigger one. The menu-bar badge added in Phase 2 now counts exactly these listings, which means the app points at a problem it has no way to resolve on its own.

**Ask:** A periodic sweep that re-pushes listings whose latest `PlatformListingPush` errored, bounded by the existing semaphore and with a backoff so a persistently broken listing isn't retried every cycle. Deliberately kept separate from the tray work — see `docs/plan-background-sync.md` §4 for why a marketplace-write behaviour change shouldn't ride along inside a "keep the app running" feature. Depends on the Etsy structural-failure detection above so the sweep can skip the pushes that can never succeed.

### Crash-recovery watchdog for the tray

**Problem:** The tray, autostart, PID file and sidecar supervisor all shipped (0.10.x tray branch). One piece of `docs/plan-background-sync.md` §6c is still unbuilt: `lib.rs` writes a `backend.stopped` marker on a deliberate Quit and clears it on launch, but nothing reads it. So an app killed by a *crash* (rather than quit on purpose) doesn't come back until the user launches it by hand — which for an unattended sync machine can be days.

**Ask:** The §6c watchdog — a lightweight external check (Scheduled Task or a second tiny process) that relaunches StockSmith `--hidden` when it finds the shell gone *and* no `backend.stopped` marker present. Small, but it's the difference between "restarts a crash" and "restarts something the user closed on purpose".

---

## Stock-take workflow

### No UI to cancel / abandon an open stock take

**Problem:** The backend already supports it — `DELETE /stock-takes/{id}` (`routers/stock_takes.py` → `services/stock_takes.delete_stock_take`) abandons an open take and refuses a closed one, and the frontend client already wraps it (`stockTakesApi.remove`, `api/stockTakes.ts`). Nothing calls `remove`: neither the list (`routes/stock-takes/route.tsx`) nor the detail slide-over (`routes/stock-takes/$stockTakeId.tsx`, whose only `ConfirmDialog` is the Approve one) has a cancel/abandon control, so an open take can only be worked to completion or left open forever.

This bit on 2026-09-02: a start-take bug created takes #3 and #4 with identical scope six seconds apart ("Materials in Filament, due for counting only"). #3 was counted against; #4 sat at 0/6 with no way to clear it from the app — it had to be deleted by hand via the API.

**Ask:** Add an "Abandon stock take" action to the detail slide-over footer (open takes only), behind a `ConfirmDialog` (`tone="danger"`) since it drops any counts entered so far. On success invalidate `["stock-takes"]` and `["dashboard-summary"]` and route back to `/stock-takes`. A row-level action on the list is optional follow-up. Separately, consider a guard against creating a second open take whose scope matches an existing open one — `_open_take_warnings` (`services/stock_takes.py:203`) already computes the overlap but only *reports* it; a create-time block for an exact-scope match would stop the start-take double-fire turning into orphaned takes.

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

### Bulk "draft all purchases" for a supplier

**Problem:** Surfaced while reviewing the `StockSmith.dc.html` design canvas — its dashboard stockout panel groups short materials by supplier and offers one button that drafts every short material in a group as a single purchase order. Today a purchase can only be drafted one material at a time, so clearing a supplier's whole shortfall means repeating the draft flow per material.

**Ask:** A backend endpoint that batches draft-purchase creation by supplier (one draft PO per supplier, one line per short material), plus a "draft all" action on the dashboard's stockout-by-supplier grouping.

### Per-material lead-time override

**Problem:** Surfaced via the design canvas, whose material Purchasing tab has an editable per-material "Lead time (days)" field. Today lead time is a single global default in Settings (`general_settings.default_lead_time_weeks`) — there's no way to say one supplier or material runs longer than the rest.

**Ask:** New `lead_time_days` field on the material (or supplier) model, used by the reorder/forecast calculation in place of the global default when set. Needs a new backend field plus a small forecast-calc change.

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

## eBay integration hardening

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

### CSV export for Orders and Purchases lists

**Problem:** Surfaced via the design canvas, which shows an "Export CSV" action in the shared list header on every list screen. Materials and Products already support CSV export/import (`CsvImportExport`); Orders and Purchases don't have an equivalent today.

**Ask:** Extend the existing `CsvImportExport` pattern to the Orders and Purchases list routes. Small — mostly a router endpoint plus reusing the existing frontend component.

### Configurable/hideable list columns

**Problem:** Surfaced via the design canvas, which puts a "Columns" button on every list screen's toolbar (Products, Materials, Orders, Purchases). No real list view currently lets a user show/hide or reorder columns — the column set is fixed per screen.

**Ask:** A shared column-visibility control for list views, persisted per user/screen. Frontend-only, but touches every list route — medium scope, worth doing once as a shared component rather than per-screen.

### Keyboard row navigation (j/k) on list views

**Problem:** Surfaced via the design canvas, which wires `j`/`k` to move a selection cursor up/down the Products and Materials list rows (with Enter to open, X to select). No list view in the real app supports keyboard row navigation today.

**Ask:** Frontend-only — add row-cursor state and key handlers to the Products/Materials list routes, matching the pattern already used for bulk-select. Small scope, but worth doing once as a shared hook if it's wanted on more than one list.

### Channel column on the dashboard's Blocked orders table

**Problem:** Surfaced via the design canvas — its dashboard Blocked-orders table has a `Channel` column (Etsy / eBay / Manual pill) beside `Placed`. The real table (`src/routes/index.tsx`, `blockedRows`) can't show it: `OrderAwaitingInventory` / `OrderAwaitingPackaging` on the `/dashboard/summary` payload carry no `platform`, so there's nothing to render.

**Ask:** Add `platform` to those two dashboard row schemas (`schemas/dashboard.py:44,55` — the order is already joined in the query), then a Channel column in `blockedRows` reusing `PLATFORM_COLORS` / `PLATFORM_LABELS`. Small — one field each side.

### Dashboard KPI date-range toggle

**Problem:** Surfaced via the design canvas, which shows a This week / Month / Quarter toggle above the dashboard KPI cards. The real dashboard summary is fixed to "this week" with no way to see the same KPIs over a longer window.

**Ask:** Extend the dashboard summary endpoint to accept a range parameter and wire a toggle to it. Small backend query change; frontend is mostly state plumbing.

### Manual-order source/channel tagging

**Problem:** Surfaced via the design canvas, whose manual order composer includes a "Source" field (`Manual · direct sale`, `Etsy · keyed by hand`, `eBay · keyed by hand`) so a hand-entered order can be distinguished from a pure direct sale. Today `/orders/new` has no channel field at all — every manually created order is attribution-less.

**Ask:** Add a source/channel field to manual order creation, distinct from the automatic platform-sync channel tagging. Small — a new field on the order model plus a select in the `/orders/new` form.

---

## Colour & settings polish

### Let a new colour's hex be set at the point of use, or link to the colour settings page

**Problem:** When you set a colour on a material (Details tab "Colour" field, `CreatableSelect` backed by `coloursApi`), creating a new colour only records its name — `find_or_create` never sets `hex_code`. So the only way to give a colour a hex (which drives the list/detail chip) is to go to Settings → Reference data → Colours afterwards and edit the row. Nothing on the material form tells you that, or that the colour you just picked has no hex.

**Ask:** On the material colour field: when the user is creating a *new* colour, also offer an optional hex input and pass it through (needs `find-or-create` / the material create/update path to accept `hex_code`, or a follow-up `PATCH /colours/{id}`). When an *existing* colour with no `hex_code` is selected, show a small inline "no colour chip set — add one" link to `/settings?tab=reference` (the Colours table). Keeps the reference table as the source of truth while removing the dead end.

### Colour chip in the material slide-over hero slot

**Problem:** The Materials list now shows a colour chip (from the linked colour's `hex_code`) in place of a thumbnail for materials with no uploaded image. The material detail slide-over (`routes/materials/$materialId.tsx`) still only shows the 192px hero image box with a plain "No image" placeholder — it doesn't fall back to the colour chip the way the list does.

**Ask:** In the detail panel's hero area, when `material.image_path` is null but `colour_hex` is set, render the colour as a filled swatch in the same box (keep the upload/replace controls). Frontend-only — `colour_hex` is already on `MaterialRead`. Mirror the list's image → chip → neutral precedence.

### Colour swatches in the Colours settings table

**Problem:** Settings → Reference data → Colours (`ReferenceDataTable`, `settings.tsx:239`) lists each colour by name with an editable "Hex code" text field, but shows no visual swatch — you can't see at a glance which colours have a hex or what they look like.

**Ask:** Render a small colour chip next to each colour name in that table, filled from `hex_code` (neutral/empty when unset). Likely a small `renderCell`-style addition to `ReferenceDataTable` or a colours-specific column; frontend-only, `hex_code` is already in `ColourRead`.

### Settings toggles render as checkboxes, not switches

**Problem:** The design canvas draws every boolean setting as a pill switch. The real settings forms (`BackgroundSyncSettings`, `BackupSettings`, `StockCountSettings`, the reference-table checkbox fields) use a bare `<input type="checkbox">`.

**Ask:** A small `Switch` that renders `<input type="checkbox">` styled as a track+knob (peer-checked Tailwind, `role` unchanged so `getByRole("checkbox")` tests keep passing). Purely visual; swap it in across the settings forms.
