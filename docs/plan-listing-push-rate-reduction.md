# StockSmith — Listing-Push Rate Reduction: Stages Plan

## Status

**Stages 1–4 implemented 2026-09-07** (PR #55). **Structural-failure marking (the Stage 4
rider) and Stage 5 implemented 2026-09-07** on a follow-up branch. Stages 6–7 remain
pending — pick them up only if the Stage 2 API-usage numbers show the daily budget is
still tight in practice. What landed:

- **Stage 1** — `_RATE_LIMIT_MAX_SLEEP_SECONDS` cap (120s) in both adapters; `_tick`
  bounded by `asyncio.wait_for` (`_COMMIT_SYNC_TIMEOUT_SECONDS` = 600) with a recorded
  failed run on timeout; a 401 surviving a token refresh now raises `PlatformAuthError`.
- **Stage 2** — `platform_api_usage` service + `platform_api_usage` table; adapters call
  `record()` from `_request_once`; `_push_now` defers over the 80% soft limit and the
  reconcile sweep stands down at the 95% hard limit; usage shown in the Sync panel
  (`PlatformStatus` / `PlatformSyncSummary`).
- **Stage 3** — `listings.last_pushed_qty` / `last_pushed_at`; `_push_now` skips a listing
  whose watermark already equals the resolved quantity (no time-based escape — the sweep
  re-asserts); `EtsyAdapter.push_listing_quantity` skips the PUT when the GET shows the
  quantity already matches. eBay's push does no GET-of-quantity, so 3c is Etsy-only; the
  Stage 3b watermark skip covers both.
- **Stage 4** — `listing_reconcile` loop (hourly, started from lifespan + restore),
  `_MAX_PER_RUN` = 25 per platform, `_STALE_AFTER` = 12h; also drives
  `platform_api_usage.flush()` and `listing_push.drain_deferred()`.
- **Stage 4 rider — structural-failure marking.** `listings.structural_push_block` /
  `structural_push_block_at` (migration `f3b1d7e05a29`). `EtsyAdapter.push_listing_quantity`
  raises `PlatformListingStructuralError` when its GET shows an empty `quantity_on_property`
  alongside >1 live product (the "quantity must be consistent across all products" dead
  end), before the PUT. `listing_push._push_one` persists the marker (message names the
  fix) and writes **no** `PlatformListingPush` row; a later confirmed push clears it.
  `_push_now` and `listing_reconcile._listings_to_check` skip marked listings;
  `listing_reconcile._marked_to_reprobe` re-probes them on a 24h cadence.
  `sync_status._failing_push_counts` excludes them and `_structurally_unpushable_counts`
  surfaces them separately on `PlatformSyncSummary` (+ `GET /platforms/{platform}/
  structural-push-blocks` and a Sync-panel list) so the badge points at the listing to
  fix. Both `docs/backlog.md` entries this completes ("…doesn't vary by variation" and
  "Periodic reconciliation for failed listing pushes", the latter via Stage 4) are
  deleted.
- **Stage 5** — `enqueue_for_material` now schedules ONE debounced job per material
  (`_pending_materials`, `_debounced_material_diff`) instead of one debounced push per
  affected product/variant. When it fires, `_diff_and_enqueue_material` runs a single
  batched buildability pass (`_resolve_targets_bulk` over
  `get_max_buildable_by_product` + `compute_variants_buildability_bulk` +
  `compute_max_sellable_bulk`), diffs each affected listing's resolved target against
  `last_pushed_qty`, and `_enqueue`s only the entries that moved — the same set of real
  pushes Stage 3 alone would have produced, reached in one session instead of ~N.
  `quiesce()` cancels the new jobs too. Callers (`costing.recompute_material`,
  `kitting.py`) unchanged — the `session` arg is now unused (diff runs later on its own).

Build plan, written 2026-09-07 after diagnosing an Etsy auto-sync stall: the sync loop
was parked ~6 hours inside a single `asyncio.sleep(Retry-After)` after the day's Etsy API
budget was exhausted by listing-push fan-out. eBay was unaffected (independent loop), so
it read as an Etsy fault.

**Root cause of the volume**, established from the live DB (`platform_listing_pushes`,
`platform_sync_runs`) and `backend.log`:

- `listing_push` fans out on an **input** change (a material's quantity), never on the
  **output** changing (the number we would actually send to the marketplace).
  `costing.recompute_material` and `kitting.py`'s reservation/consumption path both call
  `listing_push.enqueue_for_material`, which enqueues a debounced push for **every
  product/variant whose build or kitting BOM references that material**.
- Shared materials are broad: thermal labels (consumed on every shipment) fan to 37
  product/variant rows; common filament colours to 56–132. One ordinary event — an order
  allocated, a build, an order shipped — repushes a large slice of the catalogue.
- `EtsyAdapter.push_listing_quantity` always does GET-inventory **and** PUT-inventory,
  even when the target quantity already equals what Etsy holds. Every fan-out entry is ≥2
  API calls regardless of whether anything changed.
- On 2026-09-07, five such events between 09:07 and 11:58 produced ~1,100 pushes ≈ 2,400+
  API calls, on top of the 15-minute receipt syncs and per-receipt financial enrichment.
  Etsy's daily budget ran out ~11:58; the next receipt-sync tick got a 429 with
  `Retry-After: ~7189s` and `_authed_request` slept on it, parking the whole Etsy loop.

**Read to build this:** `backend/app/services/listing_push.py` (all of it),
`backend/app/services/sync_scheduler.py`, `backend/app/services/order_sync.py`,
`backend/app/services/platforms/etsy.py` (`_authed_request`, `_rate_limit_delay`,
`push_listing_quantity`), `backend/app/services/platforms/ebay.py` (the equivalent retry
and push paths), `backend/app/services/costing.py` (`recompute_material`),
`backend/app/services/kitting.py` (`enqueue_for_material` call site ~785,
`compute_max_sellable`, `sync_listing_ceiling_qty` ~946), `backend/app/services/buildability.py`,
`backend/app/services/listing_sync.py`, `backend/app/models/listing.py`,
`backend/app/services/sync_status.py` (`_failing_push_counts`, the menu-bar badge),
`backend/app/routers/platforms.py` (sync endpoints).

**Not verified by running anything.** Every claim about current behaviour is read from
this repo's source or this shop's live DB/log; the effect sizes below are estimates from
that data, to be confirmed with the Stage 2 instrumentation once it exists.

---

## Sequencing rationale

1. **Stop the stall before touching volume.** Stage 1 is three small, purely defensive
   changes that would have turned the 6-hour outage into a logged failure and a resumed
   loop. It depends on nothing and should ship first.
2. **Put a hard ceiling under the problem next.** Stage 2's per-platform daily budget
   means no amount of push churn can starve order sync, so the larger structural changes
   can land without the system being one bad morning from another stall.
3. **Then cut the volume at its source**, cheapest and least invasive first: gate on the
   outgoing value (Stage 3), add the drift backstop that makes that safe (Stage 4), stop
   spawning work for listings that won't change (Stage 5), apply the down-now/up-later
   asymmetry (Stage 6), and finally — only if headroom is still short — make the fan-out
   itself constraint-aware (Stage 7).

Each stage is independently shippable and leaves the system in a better state than it
found it. Stages 1, 2, 3+4 are the ones that matter; 5–7 are diminishing returns and can
stop wherever the Stage 2 numbers say the budget is comfortable.

| Stage | Change | Schema | Depends on | Expected effect |
|---|---|---|---|---|
| 1 | Safety rails: cap `Retry-After`, bound `_tick`, classify 401 | none | — | No more multi-hour stalls; auth failures auto-disable as designed |
| 2 | API-call instrumentation + per-platform daily budget | small | — | Order sync can never be starved by push volume |
| 3 | Gate pushes on the outgoing value (`last_pushed_qty`) + skip no-op PUT | migration | — | Dominant call reduction (est. 10–20×) |
| 4 | Reconciliation sweep (drift + errored-push backstop) | none | 3 | Externally-edited listings still get corrected; delivers a standing backlog item |
| 5 | Decide before enqueue: one batched pass per material | none | 3 | Removes ~N asyncio tasks + sessions + buildability calls per material tick |
| 6 | Directional cadence + deadband (down now, up on a slow sweep) | none | 3, 5 | Cuts build/sell oscillation churn |
| 7 | Constraint-aware fan-out | cache/index | 3, 5 | Common case (label decrement) enqueues nothing at all |

---

## Stage 1 — Safety rails (stop the stall)

Independent of rate reduction. One PR.

- **1a. Cap the honored `Retry-After`.** In `EtsyAdapter._rate_limit_delay` (and eBay's
  equivalent), clamp the returned delay to a ceiling (propose 120s). In `_authed_request`,
  if the server asks for longer than the ceiling, do **not** sleep the difference — raise
  `PlatformRateLimitError` immediately so the caller records a failed run and the loop
  moves on. Rationale: `Retry-After` on a daily-quota 429 is the seconds to midnight-UTC
  reset (~2h); obeying it literally, up to `_MAX_RATE_LIMIT_RETRIES` times, is the ~6h
  park.
- **1b. Bound `_tick`.** In `sync_scheduler._tick`, wrap the `commit_sync` call in
  `asyncio.wait_for(..., timeout=T)` (propose T = 10 min). On `TimeoutError`, log it,
  let the existing failure path record a run, and return so `_loop` continues. Rationale:
  `_loop` awaits `_tick` to completion before it sleeps/iterates; any unbounded await
  inside a tick freezes the platform's whole loop with no periodic log line and no
  "loop died" marker.
- **1c. Classify 401 as auth failure.** `fetch_orders_since` (etsy.py:355) and the push
  paths raise generic `PlatformSyncError` on any non-200, including 401
  `invalid_token`/`access token is expired`. Raise `PlatformAuthError` for 401 so
  `sync_scheduler._record_auth_failure` actually counts it and the
  `_MAX_CONSECUTIVE_AUTH_FAILURES` auto-disable engages instead of retrying a dead
  connection every interval forever.

**Verify:** unit-test that a 429 with a huge `Retry-After` raises rather than sleeps; that
a `_tick` whose `commit_sync` hangs past the timeout still lets `_loop` reach its
`asyncio.sleep`; that a 401 increments `consecutive_auth_failures`. Regression-check
against `test_sync_scheduler_resilience.py`.

---

## Stage 2 — Instrumentation + per-platform daily budget

- **2a. Count marketplace API calls.** Wrap `_request_once` (Etsy) and the eBay
  equivalent to increment a per-platform, per-UTC-day counter. Persist it (a small
  `platform_api_usage` table keyed on `(platform, utc_date)`, or an in-process counter
  flushed periodically — the table is more useful and survives restarts). Surface
  today's count and the configured budget in the sync panel.
- **2b. Budget-aware push dispatch.** Give `listing_push` a reserve margin (propose: stop
  dispatching automatic pushes once the platform's day count passes ~80% of budget,
  leaving headroom for order sync + enrichment + the Stage 4 sweep). Over the margin,
  park debounced pushes in a pending set instead of sending, and drain them after the
  UTC-day reset, most-recently-changed first. **Order sync is never gated by this** — the
  budget throttles outbound push only.

**Verify:** with the counter in place, replay a burst (or wait for a natural one) and
confirm the count climbs, dispatch stops at the margin, and `platform_sync_runs` keeps
ticking on schedule throughout. This stage is also the measurement baseline for 3–7.

---

## Stage 3 — Gate pushes on the outgoing value

The core fix: push only when the number we would send differs from the number we last
sent.

- **3a. Resolve the column-semantics collision.** `listings.last_synced_qty` is written
  today by two writers with different meanings — `listing_push._push_one` writes the
  actually-pushed quantity, `kitting.sync_listing_ceiling_qty` writes
  expected-max-sellable/ceiling. Add a dedicated `last_pushed_qty` (+ `last_pushed_at`)
  column written **only** by `_push_one` on a confirmed push; leave `last_synced_qty` to
  its bookkeeping use (or rename it in the same migration to remove the ambiguity).
- **3b. Skip unchanged listings in `_push_now`.** After `_resolve_max_sellable`, for each
  listing skip entirely — no GET, no PUT — when `last_pushed_qty == qty`, **unless**
  `last_pushed_at` is older than a staleness horizon (propose 24h) so we still re-assert
  periodically even when nothing moved.
- **3c. Skip the no-op PUT in the adapter.** `push_listing_quantity` already GETs the
  inventory first. If the target SKU's offering already has this `quantity` and matching
  `is_enabled`, return without the PUT. Halves the cost of the pushes that do get through
  3b (e.g. first push after a restart, post-staleness re-assert).

**Verify:** from `platform_listing_pushes`, count pushes per triggering event before and
after — a shared-material tick that moves no listing's resolved quantity should produce
zero rows. Confirm `last_pushed_qty` tracks the last successful `attempted_qty`. Confirm a
listing untouched for >24h still gets one re-assert.

---

## Stage 4 — Reconciliation sweep (makes Stage 3 safe)

Once pushes are conditional on our cached value, a listing edited directly on the
marketplace would never be corrected. Add the backstop — which also finally delivers the
standing backlog item "Periodic reconciliation for failed listing pushes."

- Periodic pass (propose hourly, or piggyback the order-sync tick) over listings where
  `last_pushed_at` is stale, or a recent `PlatformListingPush` row is `error`: GET live
  quantity, compare to the freshly-resolved target, PUT only on mismatch.
- Bounded per run by the Stage 2 budget; process most-recently-changed first so a
  truncated run still did the useful work.
- **Rider (structural-failure marking):** detect the "quantity must be consistent across
  all products" / no-`quantity_on_property` case from the GET `push_listing_quantity`
  already does, mark the listing permanently unpushable, and have this sweep (and the
  badge in `sync_status.py`) skip it rather than retry a call that cannot succeed. This is
  its own backlog entry today; Stage 4 is what makes it necessary.

**Verify:** hand-edit a quantity on a sandbox/live listing, confirm the next sweep detects
the mismatch and corrects it. Confirm a structurally-unpushable listing is marked and
skipped, not retried every run.

---

## Stage 5 — Decide before you enqueue

Stage 3 stops the API calls for unchanged listings, but the app still spawns up to ~N
asyncio tasks + DB sessions and runs ~N buildability computations per material tick (N up
to ~132 for a common filament).

- Rework `enqueue_for_material`: instead of `_enqueue`-ing every affected
  `(product, variant)`, schedule **one** debounced job per material that does a single
  batched buildability pass for all affected products, diffs the resolved target against
  `last_pushed_qty`, and enqueues only the entries that actually changed.
- Keep `enqueue_for_owner` / `enqueue_for_product` as they are (already single-target).

**Verify:** instrument task/session creation per material tick; confirm it drops from ~N
to O(1) scheduling + one batched query, with the same set of real pushes coming out the
far end as Stage 3 alone would have produced.

---

## Stage 6 — Directional cadence + deadband

The asymmetric rule, done as a priority scheme rather than a drop scheme.

- Split enqueues by direction relative to `last_pushed_qty`:
  - **target < last_pushed** (oversell risk): dispatch now, short debounce, ahead of
    upward pushes in the queue.
  - **target > last_pushed** (upside only): route onto the Stage 4 slow sweep, never
    within seconds.
- **Deadband, upward only:** skip the upward push unless the increase is material —
  propose Δ ≥ 1 **and** (Δ / last_pushed ≥ ~10% **or** target has returned to full
  resolved capacity). Never apply a deadband downward.
- Compare against the **fully resolved** push quantity (after ceiling and packaging
  caps), not raw buildable — otherwise the deadband thrashes against a value the cap
  immediately clamps.

**Verify:** simulate a build/sell oscillation around a threshold and confirm downward
moves push promptly while upward moves coalesce into the sweep; confirm sub-deadband
increases produce no push.

---

## Stage 7 — Constraint-aware fan-out (optional, last)

Most invasive; do it only if Stage 2's numbers still show the budget is tight.

- Precompute per listing the currently-binding constraint: on-hand finished stock /
  platform ceiling / packaging material / a specific gating raw-material id. Cache or
  index it (refresh on the events that can change it).
- `enqueue_for_material(m)` then only considers listings currently gated by `m`, or
  within the Stage 6 deadband of being gated by it. A thermal-label 5000→4999 decrement
  gates nothing and enqueues nothing.

**Verify:** confirm a decrement of a non-gating shared material produces zero scheduling
work; confirm a decrement that *does* cross a listing into label-gated territory still
enqueues it.

---

## Non-goals

- Etsy/eBay webhooks or push notifications — separate track, see
  `docs/plan-always-on-sync.md`.
- The disconnect/reconnect-clears-`auto_sync_enabled` foot-gun — related, already a
  backlog entry, not bundled here unless it is cheap to fold into Stage 1.
- Changing what quantity we advertise (the `push_buildable_capacity` semantics). This
  plan changes *when* we push, not *what*.

## Backlog cross-reference

At release prep, delete from `docs/backlog.md` whatever these stages complete —
"Periodic reconciliation for failed listing pushes" (Stage 4), and the Etsy
"quantity must be consistent across all products" permanent-failure entry (Stage 4
rider).
