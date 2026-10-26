# Multi-listing support for shared SKUs (Etsy)

Status: draft design, nothing built. Scope: Etsy first; the model change is platform-neutral, eBay/Square behaviour is untouched.

## Why

Sellers run several Etsy listings over the same physical stock: A/B tests (title, photos, price), a free-shipping variant priced higher, seasonal re-listings. All of them carry the same SKUs, and all must draw from one stock pool.

## Behaviour today (verified in code, not against a live shop)

| Area | What happens with two listings sharing a SKU |
|---|---|
| Orders | Works. `find_by_sku` matches on SKU only and the Etsy transaction parser drops the listing id, so sales from either listing decrement the same product/variant. |
| Link model | `Listing` is unique on `(product_id, variant_id, platform)` with one `external_listing_id` (`models/listing.py`). A second listing has nowhere to live. |
| Sync index | `EtsyAdapter._index_listing_skus` does `index[sku] = ref`, so the later-crawled listing silently overwrites the earlier one. |
| Push | `listing_push._push_one` writes only to `listing.external_listing_id`. The other listing keeps its old quantity, so it can oversell after stock hits 0. |
| Drift check | `check_product_sku_sync` records one match per unit; the quantity-mismatch flag covers only the winner. |
| Push status | `PlatformListingPush` and the reconcile sweep key on `(product_id, variant_id)`, so a failure on one listing cannot be told apart from another. |
| Adoption | `find_unadopted_listings` flags a listing only if a SKU is unknown. A duplicate reusing known SKUs never appears. |

The dangerous gap is push: until fixed, shared-SKU listings should not be run alongside automatic pushes.

## Design

### 1. Data model

Allow many `Listing` rows per unit and platform, one per marketplace listing.

- Replace `uq_listings_product_variant_platform` with a unique index on `(platform, external_listing_id, coalesce(variant_id, -1), product_id)` for linked rows. A unit may have several rows; a given external listing links to a unit once.
- The existing row stays the "primary" listing. Add `role` (nullable string label, e.g. "free shipping", "test B") and `is_primary` (bool, default true on existing rows; exactly one primary per unit and platform, enforced in service code).
- Rows with `external_listing_id IS NULL` (never matched) remain unique per unit and platform so `_get_or_create_listing` keeps working for the unlinked case.
- Migration: additive index swap plus two columns with defaults; no data rewrite. Remember the packaged app may already carry unmerged migrations, so check head before numbering.
- `PlatformListingPush`: add nullable `listing_id` FK. Latest-status lookups (reconcile, sync_status) key on listing id when present, falling back to the unit key for old rows.

### 2. Discovery (sync index)

- `build_listing_sku_index` returns `dict[str, list[ExternalListingRef]]` (all listings holding each SKU). Because `eBay`/`Square` adapters share the base signature, add a new `build_listing_sku_multi_index` on the base class that defaults to wrapping the single index, and override it on Etsy. This keeps eBay/Square untouched.
- `check_product_sku_sync` upserts one `Listing` row per matched ref, marks the first (stable order: active before inactive, then lowest listing id) primary if none is, and unlinks rows whose listing no longer holds the SKU (same clearing as `_unlink_stale_unit_rows`, now per row).
- `published_sku` stays per row.

### 3. Push

- `_push_now`, `push_units_now`, `reconcile_listing` already iterate `Listing` rows, so fan-out largely falls out of the model change. Verify the three `Listing.variant_id == ...` selects return all rows and that nothing calls `scalar_one_or_none` on them (known sites to audit: `kitting.py:997`, `listing_reconcile.py:153`, `draft_listing.py:201/243/273`, `product_platforms.py:199`).
- Every linked, live listing receives the same quantity. The per-listing lock in `_get_listing_lock` already serialises writes to one listing id, so two listings pushed together do not contend.
- The skip-if-unchanged watermark (`last_pushed_qty`) is per row, so it is correct per listing as-is.
- API budget: pushes scale with listing count. The daily soft-limit gate and `_deferred` queue key on `(product_id, variant_id)`; leave that, since deferral re-enqueues the whole unit.
- Stock-out: pushing 0 sets quantity 1 / disabled on each listing, as today.
- Each listing's quantity-per-property block (`PlatformPushBlockedError`) is recorded per listing, so one misconfigured listing no longer hides the other.

### 4. Orders and per-listing reporting

- Capture `listing_id` from the Etsy transaction (`tx["listing_id"]`) on `ExternalOrderLine` and add a nullable `OrderLine.external_listing_id`. Not used for matching, only for attribution.
- Backfill is not possible from stored data; new lines only (Etsy receipts could be refetched if wanted later).
- This enables the A/B comparison: units, revenue, and conversion proxy per listing. A report is out of scope for the first cut; the data capture is cheap and should land first because history cannot be recovered afterwards.

### 5. UI and adoption

- Sync-check panel: show each listing per unit with its title, state, quantity, role, and a mismatch badge per listing; the primary is marked.
- Allow editing `role`, and choosing a different primary.
- Unadopted-listings view: surface a "Duplicate of linked SKUs" group for listings whose SKUs are all known but the listing id is not linked, with one-click "link as additional listing". Adoption never rewrites the listing's SKUs in this path.
- Draft listing push (`draft_listing.py`): a new draft for a unit that already has a live listing creates an additional row instead of replacing the link (currently reads `.first()`).

### 6. Out of scope

- eBay/Square multi-listing.
- Splitting stock between listings (reserve N for the free-shipping listing). Shared pool only.
- Per-listing price or shipping management beyond what draft push already does.

## Staging

1. Order attribution only: `listing_id` on order lines (no model risk, starts accumulating history).
2. Model + migration + sync discovery + push fan-out + push-status keying, with tests for: two listings one SKU (index, link rows, both pushed, one blocked while the other succeeds, stock-out disables both, unlink when one listing drops the SKU).
3. UI: multi-listing display, role/primary editing, duplicate-listing adoption.
4. Optional per-listing sales report.

Stage 2 is the safety fix and should not wait on UI.

## Risks and open questions

- Etsy rate budget: every extra listing is one GET+PUT per stock change; confirm against the 5k/day budget notes in `plan-listing-push-rate-reduction.md`.
- `ExternalListingRef`'s single-listing assumption leaks into `base.py`; keep the multi-index additive to avoid touching eBay/Square.
- Draft/inactive listings sharing the SKU: decide whether pushes skip non-active listings (suggested: push to `active` and `inactive` as today, skip `draft`/`sold_out`/`expired` only if Etsy rejects them).
- Etsy ToS/policy on duplicate listings is the seller's concern; StockSmith only keeps stock consistent.
