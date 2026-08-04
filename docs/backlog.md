# Backlog

Informal list of improvements not yet scheduled into a plan doc.

## Variant BOM setup — conflict detection

**Problem:** In the product Variants tab, an attribute using "Material driven by this attribute" (e.g. Colourway substituting one BOM material for another) can silently collide with a different attribute's own BOM line for the same target material (e.g. a "Quantity driven" rule on the real Ivory White line). "Generate variants" then fails with a raw `IntegrityError` on `uq_product_variant_materials_variant_material`, surfaced to the user only as a generic "Internal server error" (see `backend/app/services/variants.py:145-169`, `backend/app/services/variants.py:230`, `backend/app/models/variant.py:74`).

**Ask:** Validate BOM rules across attributes before generating variants and flag the specific conflicting combination in the UI (e.g. "Colourway 'Ivory' on Lilac Purple conflicts with existing Ivory White BOM line") instead of letting it fail at commit time as an opaque 500.

## Variant setup — bulk amend BOM overrides

**Problem:** BOM quantity/material overrides are set per attribute value during initial variant generation. If a mistake is found after variants already exist (e.g. wrong material quantity for all "Large" variants), there's no way to bulk-correct it — every affected variant has to be edited individually.

**Ask:** Add a UI method to bulk-edit BOM overrides for variants sharing an attribute value (e.g. "update quantity for all Large variants") without regenerating/recreating the variants from scratch.

## Store sync test — auto-correct stock mismatches

**Problem:** When testing a store sync connection, if the store reports back as connected but its stock count doesn't match StockSmith's, there's currently no reconciliation.

**Ask:** When a sync test finds a connected store with a stock count mismatch, push the StockSmith stock count to the store to correct it.

## Menu bar — last sync time and error badge

**Problem:** There's no at-a-glance way to tell when stores last synced or whether the last sync had errors.

**Ask:** In the top menu bar, show the last sync time with stores on the right-hand side, with an alert badge if the last sync encountered errors.

## Background/tray process to keep stock sync alive

**Problem:** Platform stock sync currently only runs while the StockSmith app is open, so stock levels can drift out of sync while the app is closed.

**Ask:** Explore a system-tray background process that can start on PC boot and keep running when the main StockSmith window is closed, so platform stock sync stays alive continuously.

## eBay Platform Sync — variation column always blank

**Problem:** On a product's Platform Sync tab, the "eBay variation" column is always "—" for every unit, even when the product has multiple variants all synced under one eBay listing (e.g. IKEA BILRESA Wall Mount Plate's Button/Scroll variants). This isn't a bug specific to that product — `EbayAdapter._index_inventory_item` (`backend/app/services/platforms/ebay.py:769-776`) hardcodes `variation=None` unconditionally, because eBay's Inventory API `getInventoryItems` response doesn't carry variation info at the item level — it lives on the associated Offer, which `build_listing_sku_index` doesn't fetch per-SKU. Etsy's equivalent index does populate `variation` (`ExternalListingRef.variation`, `backend/app/services/platforms/base.py:123-127`), which is why the same column works for Etsy but not eBay.

**Ask:** Either fetch the per-SKU Offer (which carries the variation-defining specifics) when building eBay's listing index and populate `variation`, or hide/relabel the "eBay variation" column so it doesn't imply data that was never fetched.

## Variant BOM — audit existing substitutions onto un-ruled base lines

**Problem:** Found while planning the variant conflict-detection work. If a variant substitutes base line A onto material M, and M is *itself* a base BOM line that nothing substitutes away, no unique-constraint violation occurs (there's no override row for M to collide with) — but `_RESOLVED_VARIANT_BOM_SQL` (`backend/app/services/buildability.py:75-98`) then emits M twice via `UNION ALL`, and `compute_variant_buildability` takes `min()` of per-line bottlenecks against the same `current_qty` rather than summing consumption (`:232-238`). With `current_qty=10` and both lines at qty 1, it reports 10 buildable when the true answer is 5. (`cost_per_unit` sums, so cost is unaffected.)

Phase 4 of the backlog-burndown plan rejects this configuration at generation time going forward, but says nothing about rows already in the database.

**Ask:** Audit existing `product_variant_materials` for substitutions targeting an un-ruled base BOM line, and decide whether `_RESOLVED_VARIANT_BOM_SQL` should sum duplicate materials rather than emit them twice — which would fix any such rows already stored, rather than only preventing new ones.

## Variant BOM — provenance column for overrides

**Problem:** `ProductVariantMaterial` has no column distinguishing a rule-generated override from a hand-edited one. The bulk-amend feature therefore can't preserve manual edits automatically — it has to show a preview and make the human consent to each overwrite.

**Ask:** Add a `source` column (`"rule" | "manual"`) so bulk operations can leave hand-edited rows alone by default.
