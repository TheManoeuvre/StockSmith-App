# Backlog

Informal list of improvements not yet scheduled into a plan doc.

## An async session is unusable after a rolled-back flush error

**Problem:** Found while building the stock-count work. In this dependency set
(SQLAlchemy 2.0.51, aiosqlite 0.22.1, greenlet 3.5.3) there is a reproducible condition
where an `AsyncSession` becomes permanently unusable once a flush raises `IntegrityError`
and the session is rolled back. Every later statement on it — including a bare column
`SELECT` that touches no ORM object — raises
`MissingGreenlet: greenlet_spawn has not been called`. An explicit `await session.rollback()`
by the caller does not recover it; a **new** session on the same engine is fine.

Reproduces on the app's own code via `costing.create_adjustment`'s "would make current_qty
negative" path, on both the test harness's StaticPool and a production-shaped engine, and
also with no app code at all:

```python
m = Material(name="R", category=..., unit=...)
s.add(m); await s.commit()
m.current_qty = Decimal(-5)          # violates ck_materials_current_qty_nonneg
try: await s.flush()
except IntegrityError: await s.rollback()
await s.scalar(select(Material.name))  # MissingGreenlet
```

The trigger is narrow and not fully isolated: a UNIQUE violation on a session that has not
previously committed recovers cleanly, and so does a CHECK violation on an object loaded
fresh into its own session. It appears to need a prior commit on the same session. Worth
pinning down before assuming any particular fix.

**Impact today is nil**, which is why nothing has caught it: every request gets its own
session and ends at the error, so nothing reuses a poisoned one. `csv_io.py` is the one
place that catches per row and continues, and its failures are pre-flush validation
errors (`validate_qty_for_unit`) rather than flush errors, so it stays on the good path —
but a row that did violate a constraint would take the rest of the import down with a 500
rather than landing in the `failed` list, which is the shape of the bug this becomes.

**It does block a planned design.** The stock-take approve loop
(`docs/plan-stock-take.md`, Phase B) was specified to catch a refused adjustment, mark
that line for manual review, and carry on. It cannot do that on a shared session. Phase B
works around it two ways — pre-checking allocation and movement in Python so a refusal is
rare, and giving each line its own session — but the underlying fault stays.

**Ask:** Isolate the exact trigger and decide whether it's an aiosqlite 0.22 regression
(0.22 is recent and changed the connection/threading model) or a SQLAlchemy dialect
incompatibility, then pin or bump accordingly. A dependency change wants its own commit
and its own green run, not to ride along inside a feature.


## Drop the superseded `materials.colour` and `materials.category` columns

**Problem:** Two reference-table migrations deliberately left their old column in place for a
release rather than dropping it, because SQLite needs a full table rebuild to drop a column and
restoring an older backup and migrating it forward is routine. `materials.colour` was left by
`e6b21d84f309` (0.6.x) and is now several releases overdue; `materials.category` was left by
`f2a91c4d7b08` and is due next release.

Category is the one with a visible cost while it stays. The column is NOT NULL with a CHECK
accepting exactly the original seven values, so a material filed under a user-created category
has to store `'other'` there (`services/material_categories.legacy_value_for`). On the current
release nothing reads it, so nothing is wrong — but a rollback, or a backup restored into an
older build, shows those materials as "other".

**Ask:** One migration per column: drop the column and its CHECK, drop the now-unused
`LegacyMaterialCategory` enum, make `materials.category_id` NOT NULL, and delete
`legacy_value_for` along with the calls that keep the column in step (materials create/patch, CSV
import, and the rename/merge wrappers in `routers/material_categories.py`). The category-name
fallbacks in `Material.category_name` / `Material.colour_name` go at the same time, as does
the legacy branch of `services/stock_takes._material_category_sort`, which reads the enum's
declaration order to place a material whose category row is missing.

## Orphaned sidecars are detected but not reaped

**Problem:** The identity half of this is done — `/healthz` carries the build version and the shell refuses to adopt a backend that reports a different one (`backend/app/main.py:146`, `frontend/src-tauri/src/lib.rs:82`, shipped in 0.6.3). What it does on a mismatch is *stop and tell the user to close StockSmith and re-run the installer*, which is honest but is still a dead end the user has to clear by hand.

The rest of the original ask is outstanding: there's no PID file, so a sidecar orphaned by a crash or an installer is neither reaped nor distinguishable from a dev instance someone is deliberately running on port 8000.

This exists independently of the tray work, but the tray makes it much more likely to fire (see `docs/plan-background-sync.md` §2a).

**Ask:** Write a PID file alongside the sidecar so the shell can tell *its own* orphan from a foreign process, and kill-and-respawn in that case rather than refusing. Leave the refusal in place for anything it didn't start — that case is genuinely not the shell's to kill.

## Periodic reconciliation for failed listing pushes

**Problem:** `listing_push` is entirely event-driven with a 5-second debounce and no retry, so a push that fails permanently (network blip, expired token, marketplace 5xx) is never retried until unrelated stock movement happens to trigger one. The menu-bar badge added in Phase 2 now counts exactly these listings, which means the app points at a problem it has no way to resolve on its own.

**Ask:** A periodic sweep that re-pushes listings whose latest `PlatformListingPush` errored, bounded by the existing semaphore and with a backoff so a persistently broken listing isn't retried every cycle. Deliberately kept separate from the tray work — see `docs/plan-background-sync.md` §4 for why a marketplace-write behaviour change shouldn't ride along inside a "keep the app running" feature.

## Background/tray process to keep stock sync alive

**Problem:** Platform stock sync currently only runs while the StockSmith app is open, so stock levels can drift out of sync while the app is closed.

**Ask:** Planned in `docs/plan-background-sync.md` — a tray-resident app with opt-in autostart. Not yet implemented; the plan doc carries the hazards (sidecar orphaning on update, single-instance, `sync_scheduler`'s single-process assumption) and a suggested build order.

## eBay — verify Offer enrichment against a live sandbox account

**Problem:** The Offer-enrichment work (`EbayAdapter._enrich_with_offers` and friends) is covered by tests, but every one of them serves canned responses through a fake HTTP client. No request has ever gone to a real eBay account, sandbox or production. The response shapes it parses — `offer.listing.listingStatus`, `offer.status`, `product.aspects` — come from eBay's documentation rather than from observed payloads, which is exactly the class of assumption that put "eBay's token response contains a `scope` field" (it doesn't) into a release.

**Ask:** Connect the sandbox app (Client ID ends `-SBX-`) under Settings → Integrations, run a product sync check against it, and confirm: variation strings render in the same format as Etsy's, listing state maps correctly for an active/out-of-stock/ended listing, a SKU with no offer reports `no_offer`, and the fan-out stays within its concurrency cap. Fold any shape corrections back into `test_ebay_offer_enrichment.py` so the fixtures reflect reality.

## eBay listing index — use the real listing id as external_listing_id

**Problem:** `EbayAdapter._index_inventory_item` sets `external_listing_id=sku`, which isn't a listing id. The real one is now fetched during offer enrichment (`_enrich_with_offers`) but deliberately not written, because for eBay that field holding the SKU is a documented invariant with a second writer: `listing_adoption.apply_adoption` mirrors it, and `docs/listing-adoption.md` states it as a safety property.

Functionally nothing reads it except null checks, and the frontend types but never renders it. One behaviour would improve: `listing_push._get_listing_lock` would key on the real listing id, so variants of one multi-variation listing serialise instead of racing.

**Ask:** Switch `external_listing_id` to the real eBay listing id, updating `listing_adoption.apply_adoption`, `docs/listing-adoption.md` and the ~8 test assertions that encode the current invariant in lockstep. Worth doing as its own commit so it stays revertible.

## Variant BOM — audit existing substitutions onto un-ruled base lines

**Problem:** Found while planning the variant conflict-detection work. If a variant substitutes base line A onto material M, and M is *itself* a base BOM line that nothing substitutes away, no unique-constraint violation occurs (there's no override row for M to collide with) — but `_RESOLVED_VARIANT_BOM_SQL` (`backend/app/services/buildability.py:75-98`) then emits M twice via `UNION ALL`, and `compute_variant_buildability` takes `min()` of per-line bottlenecks against the same `current_qty` rather than summing consumption (`:232-238`). With `current_qty=10` and both lines at qty 1, it reports 10 buildable when the true answer is 5. (`cost_per_unit` sums, so cost is unaffected.)

Phase 4 of the backlog-burndown plan rejects this configuration at generation time going forward, but says nothing about rows already in the database.

**Ask:** Audit existing `product_variant_materials` for substitutions targeting an un-ruled base BOM line, and decide whether `_RESOLVED_VARIANT_BOM_SQL` should sum duplicate materials rather than emit them twice — which would fix any such rows already stored, rather than only preventing new ones.

## Variant BOM — provenance column for overrides

**Problem:** `ProductVariantMaterial` has no column distinguishing a rule-generated override from a hand-edited one. The bulk-amend feature therefore can't preserve manual edits automatically — it has to show a preview and make the human consent to each overwrite.

**Ask:** Add a `source` column (`"rule" | "manual"`) so bulk operations can leave hand-edited rows alone by default.

## Disconnecting a platform silently switches auto-sync off

**Problem:** `disconnect` (`backend/app/routers/platforms.py:643`) sets `auto_sync_enabled = False` along with clearing the tokens. Reconnecting doesn't restore it — the flag defaults to off for a freshly-connected shop, deliberately, so a new connection can't start unattended commits before the user has run a manual sync. The consequence is that a disconnect/reconnect cycle on an *established* connection, which is the first thing anyone tries when a platform looks stuck, quietly turns off the very thing they're trying to fix.

Nothing surfaces it. `_tick` returns early on the flag (`backend/app/services/sync_scheduler.py:78`), so there are no sync runs, no errors, and no log lines — while the settings panel goes on showing the platform as connected. The only visible difference between "auto-sync is off" and "the shop has had no new orders" is the absence of rows in a table nobody has reason to open.

Confirmed live: a shop reconnected Etsy on 14 Aug and had no sync attempt of any kind until auto-sync was manually switched back on two days later. eBay, untouched, kept syncing every 15 minutes throughout — which is what made it look like an Etsy fault.

**Ask:** Decide between two shapes and implement one. Either preserve `auto_sync_enabled` across a reconnect of a connection that already had it on (the safety argument for defaulting off applies to a *first* connection, not to re-authorising one that has been syncing for months), or keep clearing it and make the off state loud — the reconnect flow says it's now off, and the panel distinguishes "auto-sync off" from "auto-sync on, nothing to do" rather than rendering both as silence. The second is the smaller change; the first is what stops the reconnect-as-first-aid reflex from making things worse.

## Etsy quantity pushes fail permanently when a listing's quantity doesn't vary by variation

**Problem:** Etsy rejects a per-SKU quantity with `400 {"error":"quantity must be consistent across all products"}` when the listing's quantity isn't attached to a variation property. `push_listing_quantity` passes `quantity_on_property` straight back as read (`backend/app/services/platforms/etsy.py:775`), so when it comes back empty on a multi-variation listing, every product in that listing has to share one quantity and there is no per-SKU push that can succeed.

StockSmith treats this like any other push error: a WARNING, a `PlatformListingPush` row with status `error`, and a bump to the badge that `_failing_push_counts` (`backend/app/services/sync_status.py:60`) feeds. But this failure is *structural*, not transient — it will fail identically on every future attempt until the seller changes the listing's variation setup on Etsy, which is not something the app can do or even ask for. The badge therefore accrues a count that no retry will ever clear, and the message the user sees is Etsy's raw 400 body, which doesn't say what is wrong or what to change.

Live on this shop: 7 failing pushes across two listings, both with several variants mapped to one listing id and an identical `external_quantity` on all of them (`listings` rows for products 12 and 15) — the signature of exactly this configuration.

This matters more once the periodic reconciliation sweep above exists: a sweep that re-pushes every errored listing would retry these forever, burning quota on a call that cannot succeed.

**Ask:** Detect the condition rather than discovering it in a 400. The GET that `push_listing_quantity` already performs carries everything needed — an empty `quantity_on_property` alongside more than one non-deleted product means no per-SKU push is possible. Fail fast with a message naming the fix ("this Etsy listing's quantity doesn't vary by variation — enable it on the listing, or the variants can't be stocked independently"), and mark the failure as permanent/structural so it's distinguishable from a transient one: the badge can direct the user to the listing that needs changing, and any future retry sweep can skip it instead of hammering it.

## Marketplace listing profiles have no bulk-apply action

**Problem:** `ListingProfile` + `ProductPlatformSettings` (`backend/app/models/listing_profile.py`) already model exactly what tools like Sellbrite/LitCommerce call a listing "template" — a named bundle of taxonomy, shipping profile, policies, condition and processing time, resolved per product with a shop-wide default fallback (`listing_profiles.resolve_profile`). What's missing is bulk assignment: there is no way to select several products and set their profile or `is_target` in one action, so changing the shipping profile across twenty products is still twenty edits even though the data model was explicitly built to avoid that (see the model's own docstring).

**Ask:** A bulk-update action — select N products, apply a profile id and/or `is_target` to all of them — built on the existing resolution logic, no new schema. Worth pairing with an auto-apply rule ("new products in category X get profile Y") so the assignment doesn't go stale as the catalogue grows; `listing_profile_backfill.py`'s one-time "suggest profiles from Etsy" pass is the closest existing analogue, but it only runs once rather than keeping up going forward.

## No single-source listing creation across Etsy and eBay

**Problem:** `create_draft_listing` (`backend/app/routers/platforms.py:1030`) and `build_draft` (`draft_listing.py`) already take a `platform` parameter and share the same profile/copy-resolution pipeline for both marketplaces, but the frontend only presents one platform's draft flow at a time. A product meant for both shops has to be built twice, once per platform, even though nothing platform-specific happens until the final adapter call.

**Ask:** Extend the existing draft-builder with a combined form that shows both platforms' readiness/blockers side by side and a "create on both" action that calls `build_draft` for each. Keep Etsy's `taxonomy_id` and eBay's `category_id` as the separate fields they already are on `ListingProfile` — the two vocabularies genuinely don't map onto each other — just surface both instead of switching tabs.

## No bulk CSV import/export for listing settings

**Problem:** `csv_io.py` round-trips materials, products and stock-takes, but nothing at the listing layer — `ListingProfile` assignment, `is_target`, and per-platform copy overrides on `ProductPlatformSettings` can only be edited one product at a time in the app.

**Ask:** A per-platform CSV (mirroring the profile model's own platform split, since Etsy and eBay fields don't unify) covering profile assignment, `is_target` and per-platform copy, exported/imported/re-synced the same way the materials CSV already round-trips. Lower priority once the bulk-apply action above exists, since that covers most of the same "repeat one change across many products" need in-app; this is for batch edits done in a spreadsheet outside the app. Coordinate with any future BOM/variant CSV work (`docs/roadmap.md` finding 6) on shared conventions (id vs. name keys, per-entity file shape) rather than inventing them twice.

## No per-channel profitability reporting

**Problem:** `costing.py` already computes accurate per-product cost, and orders carry their originating platform, but nothing rolls sales or margin up by marketplace — there's no way to see whether Etsy or eBay is actually the more profitable channel.

**Ask:** A report or dashboard panel showing margin and sales by platform, built entirely on existing costing/order data — no new cost logic needed, just an aggregation by platform.

## Etsy listings have no soft quality warnings, only hard blockers

**Problem:** `draft_readiness.py` validates the fields a draft cannot be created without, but says nothing about a technically-valid listing that's still weak — few tags, no alt text, a thin description.

**Ask:** Extend readiness with a non-blocking warnings list surfaced alongside the existing blockers, reusing the same validation framework rather than adding a parallel one.

## Draft listings only ever carry one image, and there's no way to reuse images across products

**Problem:** Two separate gaps in the same area.

First, `build_draft` (`draft_listing.py:169-180`) only queries `AssetType.main_image` when assembling a draft's image list — one hero image, always `rank=1`. But `AssetType.listing_image` already exists in the enum (`backend/app/models/asset.py`) and is already treated as satisfying "has an image" by `draft_readiness.py:173` and `catalogue_compatibility.py:83` — the schema half-supports multiple images per product already, the draft-builder just never reads the second type. There's also no upload UI for anything beyond the single hero image.

Second, every `ProductAsset` belongs to exactly one `product_id` (optionally one `variant_id`) — there is no concept of an image that exists independently of a product. A maker who reuses the same background, prop, or packaging shot across many listings has to upload it separately to every product today, and updating it means re-uploading it everywhere it's used by hand.

**Ask:** Two parts, worth landing separately.

1. *(small)* Have `build_draft` collect `AssetType.listing_image` alongside `main_image`, ordered by `display_order`, and add upload UI for additional (non-hero) images per product. This is most of the day-to-day value and is close to free — the schema and readiness checks already assume it.
2. *(larger)* A shared image-library concept: images uploaded once, independent of any product, that can be selectively attached to any product's image set at a chosen position — by reference, not by copy, so editing the library image updates every listing that includes it on its next push. Needs a new model, storage handling alongside the existing `file_storage.py`, a library-management UI, and `build_draft`'s image-gathering extended to merge a product's own images with its selected library images in the resolved order.
