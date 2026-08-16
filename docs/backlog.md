# Backlog

Informal list of improvements not yet scheduled into a plan doc.

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
