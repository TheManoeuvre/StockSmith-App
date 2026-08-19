# StockSmith — Stock Take & ABC Classification: Implementation Plan

## Context

StockSmith tracks material and finished-goods quantities as derived state: material
`current_qty` is replayed from a ledger (`recompute_material`), product/variant
`current_stock` is a counter with a ledger beside it. Both drift from physical reality
— breakage, miscounts, unrecorded consumption — and today the only correction is a
one-item-at-a-time manual adjustment with a free-text reason. Nothing groups those
corrections into a counting session, nothing records when an item was last counted, and
nothing tells you which items are due.

This plan adds a **stock take**: a scoped, server-persisted cycle count that snapshots
expected quantities, collects counts in-app or via CSV, shows variances for review, and
commits them as adjustments through the existing ledger paths. It also builds the **ABC
classification** the reminder cadence depends on, which does not exist in StockSmith
today.

Delivered in two phases (per sign-off): **Phase A** ships classification, per-item count
tracking, and the overdue list, standing on its own. **Phase B** ships the stock-take
lifecycle, CSV, and standing variances on top.

When implementation starts, this document should be committed as
`docs/plan-stock-take.md`, matching `docs/plan-phase0-phase1.md` and
`docs/plan-marketplace-integrations.md`.

---

## What the codebase already gives us

Reuse these rather than building parallel machinery. Every one was confirmed in the
source, not assumed.

| Need | Existing thing | Path |
|---|---|---|
| Commit a counted material qty | `create_adjustment(mode=set, value, reason)` — writes a `MaterialAdjustment` then `recompute_material` | `backend/app/services/costing.py:105` |
| Commit a counted product/variant qty | `create_stock_adjustment(mode=set, ...)` — enforces the `allocated_qty` floor, fires `listing_push`, writes a `ProductStockEvent` | `backend/app/services/stock_adjustments.py:24` |
| "This row is a physical recount" | `MaterialAdjustmentMode.set` / `StockAdjustmentMode.set` with `target_qty`, documented as exactly this, and their `qty_delta != 0 OR mode='set'` constraint already permits a confirming zero-delta count | `models/material.py:119`, `models/stock_adjustment.py:15` |
| Whole-number qty validation for `each` units | `validate_qty_for_unit` | `backend/app/services/validation.py:10` |
| CSV export/import conventions | `csv.DictWriter`/`DictReader`, `utf-8-sig`, `enumerate(reader, start=2)`, `{created, updated, failed:[{row,error}]}` | `backend/app/services/csv_io.py` |
| CSV transport | `downloadCsv`, `uploadCsv`, `CsvImportResult` | `frontend/src/api/client.ts:140-170` |
| Reference-table CRUD (for product categories) | `_reference_crud.py` list-with-usage / patch / delete-if-unused / merge, plus `services/reference_data.py` | `backend/app/routers/_reference_crud.py` |
| Two-step preview→apply UI | `BulkBomAmendModal` — one endpoint, `mutate(apply: boolean)` | `frontend/src/components/products/BulkBomAmendModal.tsx` |
| Sparse-override settings pattern | `platform_field_limits` — ships empty, code defaults win unless overridden | `backend/app/services/platform_limits.py` |
| "Needs attention" surface | `DashboardSummary` + conditional sections | `backend/app/schemas/dashboard.py:61`, `frontend/src/routes/index.tsx` |
| Multi-select category filter UI | `Set<MaterialCategory>` checkboxes + one `useMemo` filter | `frontend/src/routes/materials/index.tsx:63` |
| Qty input normalisation | `normalizeQtyForUnit`, `wholeNumberStepFor`, `roundQty` | `frontend/src/lib/format.ts` |
| Buffered editor form | `useEditableCopy` + `SaveButton` + dirty-registry path | `frontend/src/hooks/useEditableCopy.ts` |
| Test scaffolding | in-memory SQLite via `StaticPool`, FK enforcement, a `pushes` fixture that records listing pushes | `backend/app/tests/conftest.py` |

**Note:** `zod` and `react-hook-form` are in `frontend/package.json` but imported nowhere.
Forms here are controlled `useState` + native `required` + `ErrorBanner`. Follow the
codebase, not the manifest.

---

## Flagged for sign-off

These surfaced during exploration and are not covered by the decisions in the brief.
None block the build; each changes what the work is worth.

**Status:** Phase A shipped. Flags 1 and 3 are now **resolved** (see each); the rest
stand as recorded.

1. **RESOLVED — no responsive work; CSV is the workshop path.** The installed app runs
   its backend as a Tauri sidecar bound to `127.0.0.1` (README, "Known limitation"), so
   no second device can reach it. Responsive tables would therefore be built for a device
   that cannot currently load the app at all, and reaching one is an *architecture*
   change (restoring the Tailscale topology from `docs/plan-phase0-phase1.md`), not a CSS
   change. **CSV export/import is the away-from-the-PC counting path** — print the sheet
   or fill it on a tablet, upload it back.

   Server-side draft state is still built as specified: it survives app restarts, crashes
   and updates, and it is the prerequisite if networked access ever returns.

   One cheap hedge is kept: the count sheet table gets `overflow-x-auto` so it scrolls
   inside its own container rather than pushing the page sideways. One class, no
   architecture, and it stops the widest table in the app being the one that breaks first
   if a narrow window is ever used.

2. **There is no user identity in StockSmith.** Auth is a single shared password with no
   users table (`backend/app/deps.py:38`). The take log records date, scope and
   per-line outcomes; the "user" field in the brief has nothing to populate it. Left out
   rather than stubbed.

3. **RESOLVED — allocated stock is physically absent, so any short count on an allocated
   line goes to manual review.** Confirmed with the user, whose reasoning is the whole
   point: units picked and boxed for an order leave the shelf but stay in `current_stock`
   until the order is marked shipped (`_allocate_line` moves only `allocated_qty`;
   `ship_line` is the only place `current_stock` is decremented). So a shelf count
   legitimately under-reports by however much is sitting boxed by the door.

   The failure this prevents is not the one that errors. `create_stock_adjustment` raises
   400 only when `new_stock < allocated_qty`. A product with 10 on hand and 5 allocated,
   counted as the 5 loose units, lands *exactly* on the floor — the check passes, the
   adjustment applies, and five real boxed units are written off while the take reports
   success. **Silent data loss that looks like a clean count.**

   **Rule:** for a finished-goods line with `allocated_qty > 0`, any counted value below
   `expected_qty` is a conflict for manual review, whatever the number — not just one
   below the floor. Counts at or above expected are unaffected. Lines with no allocation
   auto-adjust as normal, which is the overwhelming majority.

   The cost is accepted noise: every product with an open order produces a review line if
   the boxed units weren't counted. That is the correct trade — it is exactly the "two
   different truths" case the spec reserves manual review for, and the alternative is
   losing stock silently. The count sheet mitigates it by showing the allocated figure
   inline (see Count sheet below), so the counter can include boxed units and avoid the
   variance altogether.

   A material adjustment that would go negative is handled the same way: caught, marked a
   conflict, never a failed approval.

4. **Line granularity for finished stock is the variant, not the product.** "A product
   with active variants never accumulates its own `current_stock` — builds always target
   the variant row" (`backend/app/routers/products.py:125-131`). So a product with 8
   variants produces 8 count lines. **Bundle products are excluded entirely** — they hold
   no stock of their own, their `ready_to_ship` is derived from components.

5. **On day one of Phase A every item reads as "never counted"**, since nothing has ever
   recorded a count date. Displayed honestly as "Never counted" and sorted first, rather
   than back-dated to fake a clean slate.

6. **The stock-take CSV is keyed on ids, deviating from a documented convention.**
   `csv_io.py:72-74` deliberately exports names, not ids, because ids "aren't portable
   between machines". That reasoning doesn't apply here: this file is scoped to one take
   on one database and round-trips within hours. Keyed on `line_id` with `item_type` and
   `item_id` carried as a cross-check.

7. **Marketplace pushes on bulk approval need no new work.** Approving a 100-line take
   fires that many `listing_push.enqueue_*` calls, but pushes are keyed per
   product/variant, debounced 5s, and capped by a semaphore
   (`backend/app/services/listing_push.py:33-59`). The existing machinery absorbs it.

---

## Phase A — ABC classification, cadence, and the overdue list

### Data model

**New: `product_categories`** — mirrors `material_types` exactly (`id`, `name` unique,
`created_at`), plus `products.product_category_id` FK `ON DELETE SET NULL`. Products have no
type or category today; this gives them one, used by both the ABC Type tier and the
stock-take scope filter.

**Classification columns** (all nullable — NULL means "inherit"):

- `materials.abc_class`, `products.abc_class` — `portable_enum(ABCClass, name="abc_class")`, values `A`/`B`/`C`. Item-level override.
- `materials.stock_take_interval_days`, `products.stock_take_interval_days` — Integer. Per-item cadence override, beats the tier cadence.
- `materials.last_stock_take_at`, `products.last_stock_take_at`, `product_variants.last_stock_take_at` — `DateTime(timezone=True)`, nullable. Variants need their own because they hold their own stock.

`last_stock_take_id` is deliberately **not** added in Phase A — `stock_takes` doesn't
exist yet. Phase B adds it as a nullable FK alongside.

**New: type-tier override tables** (both sparse — a row exists only where a tier is
assigned):

- `material_category_abc` — `category` (the `MaterialCategory` enum, PK) → `abc_class`.
- `product_category_abc` — `product_category_id` (PK, FK CASCADE) → `abc_class`.

**New: `abc_tier_settings`** — sparse cadence overrides: `scope` (`material`/`product`),
`tier` (`A`/`B`/`C`), `interval_days`. Unique on `(scope, tier)`. **Ships empty**; code
defaults win unless overridden, following the pattern `platform_limits.py` argues for
over the seeded-rows approach.

**Baseline defaults** — two columns on `general_settings` (the documented home for
app-wide defaults): `default_material_abc_class`, `default_product_abc_class`, both
`server_default='C'`. Follow the migration shape in
`alembic/versions/a1b2c3d4e5f6_add_materials_forecast_fields.py:27-42`.

### Resolution logic — `backend/app/services/abc.py` (new)

```
_DEFAULT_INTERVAL_DAYS = {A: 30, B: 60, C: 90}   # shipped defaults, overridable

resolve_abc_class(material) = material.abc_class
                           ?? material_category_abc[material.category]
                           ?? settings.default_material_abc_class

resolve_abc_class(product)  = product.abc_class
                           ?? product_category_abc[product.product_category_id]
                           ?? settings.default_product_abc_class

resolve_interval_days(item) = item.stock_take_interval_days
                           ?? abc_tier_settings[scope, tier]
                           ?? _DEFAULT_INTERVAL_DAYS[tier]
```

Symmetric across materials and products, as the brief assumed. A variant inherits its
product's class and interval — variants get no classification of their own, only their
own count date.

Provide **batch** forms (`resolve_abc_classes(session) -> dict`) alongside the
single-item ones; every list endpoint in this codebase pre-builds `*_by_product` dicts
to avoid N+1, and the overdue query must too.

`compute_due_for_count(session)` returns per item: scope, id, name, resolved tier,
interval, `last_stock_take_at`, `days_overdue` (NULL last date sorts first as "never
counted"). Excludes inactive items and bundle products.

### API

New `backend/app/routers/stock_takes.py`, `prefix="/stock-takes"`,
`dependencies=[Depends(require_auth)]`, registered in `main.py` with `prefix="/api/v1"`.
Phase A adds one route; declare literal segments before any `/{id}` route added later.

- `GET /api/v1/stock-takes/overdue` → `list[DueForCountItem]`

New `routers/product_categories.py`, copied from `routers/material_types.py` (list /
create / find-or-create / patch / delete / merge, all delegating to `_reference_crud.py`).

Extend `routers/fee_config.py` (which owns the `/settings` prefix):
`GET|PUT /settings/stock-count-settings` — baseline classes, the three interval
overrides per scope, and the category/product-category tier assignments, in one payload.

Extend `DashboardSummary` with `items_due_for_count: list[DueForCountItem]`.

### UI

- **Settings → Reference tab**: add a Product Categories table via the existing
  `ReferenceDataTable` (`frontend/src/components/reference/`).
- **Settings → General tab**: a `StockCountSettings` panel modelled on
  `components/settings/ForecastSettings.tsx` — two baseline selects, six interval
  inputs, and tier assignment for the seven material categories and each product category.
- **Product detail** (`routes/products/$productId.tsx`) and **material detail**
  (`routes/materials/$materialId.tsx`): an ABC class select, an interval override input,
  and a read-only "Last counted" line showing the resolved tier and effective interval,
  so it's clear which level a value came from.
- **Products list**: a product-category column and filter, matching the materials list's
  category filter.
- **Dashboard**: a "Due for counting" section following the existing conditional-section
  pattern, linking to the (Phase B) scope picker with `overdue_only` preselected.

---

## Phase A.1 — A manual "Set" adjustment counts as a count

**Why:** the app already has a way to record a physical count — the Set mode on the
material and product adjust forms, which the models document as "a physical stock count"
and store with the `target_qty` it was set to. Today that has no effect on the counting
schedule, so recounting an item by hand leaves it still showing as due tomorrow. That is
the duplicate activity this removes.

**Rule:** `mode='set'` updates `last_stock_take_at` to now. `mode='adjust'` never does —
a signed delta for breakage tells you what changed, not that the total is now right, and
treating it as a count would silently vouch for a figure nobody verified.

Unconditional on Set, per sign-off. The known cost: using Set to correct an earlier
data-entry mistake rather than to record a count will reset that item's clock, and it
won't be flagged again for a full cycle. Accepted as the cheaper error, and worth a line
in the field's help text rather than a tickbox on a three-field form.

**Where:**
- `backend/app/services/costing.py::create_adjustment` — set `material.last_stock_take_at`
  when `mode is MaterialAdjustmentMode.set`, in the same transaction as the adjustment.
- `backend/app/services/stock_adjustments.py::create_stock_adjustment` — same for the
  resolved owner (`Product` or `ProductVariant`, whichever holds the stock).
- Both already run inside a single commit, so no new transaction handling.
- `last_stock_take_id` stays NULL for these — Phase B adds that column, and a hand
  adjustment genuinely belongs to no take. NULL there reads as "counted outside a take",
  which is accurate rather than missing.

**Backfill migration:** set each item's `last_stock_take_at` from the most recent
`created_at` among its own `mode='set'` adjustments (`material_adjustments` keyed by
`material_id`; `stock_adjustments` keyed by `product_id`/`variant_id`, matching how
`StockAdjustment` records ownership). Items never recounted stay NULL and still read
"Never counted". This is what stops the first due-list being the entire catalogue in
arbitrary order — items genuinely recounted at some point start with a real date, so the
list is a usable priority order on day one rather than a wall.

One-way and data-only, so it wants its own migration rather than riding along with a
schema change; the downgrade is a no-op (the columns it writes are dropped by the Phase A
migration's own downgrade).

---

## Phase B — Stock take lifecycle

### Data model

**`stock_takes`**
- `id`, `status` — `portable_enum(StockTakeStatus)`, `open`/`closed`
- `includes_materials` bool, `includes_products` bool, `overdue_only` bool
- `scope_description` String — rendered at creation, for the log
- `started_at`, `closed_at` (nullable), `notes` (nullable)
- `created_at`, `updated_at`

`open_days` is computed from `started_at` on read, never stored. Visibility only — no
auto-expire, per decision 2.

**`stock_take_lines`**
- `id`, `stock_take_id` FK CASCADE
- Owner, mirroring `StockAdjustment`'s shape: `material_id` FK nullable, or
  `product_id` FK nullable + `variant_id` FK nullable. `CheckConstraint` enforcing
  exactly one of (`material_id` set) / (`product_id` set).
- `expected_qty` `Numeric(14,4)` — the **snapshot at take-start**
- `allocated_qty_at_start` `Numeric(14,4)` nullable — snapshotted alongside, for
  finished-goods lines only. Shown on the count sheet ("12 expected — 5 already picked
  for orders") so the counter knows the shelf will look short by that much and can go and
  find the boxed units. Stored rather than read live at review time so the sheet, the
  CSV and the review all describe the same moment.
- `counted_qty` `Numeric(14,4)` **nullable — NULL means "not counted"**, the distinction
  the whole "no count = no change, no re-date" rule rests on
- `notes` String nullable
- `status` — `pending` / `counted` / `applied` / `conflict` / `accepted_system` / `skipped`
- `system_qty_at_approval` `Numeric(14,4)` nullable — recorded on conflict so review can
  show all three numbers (snapshot / counted / current)
- `conflict_reason` String nullable — "moved since snapshot", "5 units allocated to open orders"
- `material_adjustment_id` / `stock_adjustment_id` — nullable FKs `ON DELETE SET NULL`,
  two columns for two tables, exactly as `ProductStockEvent` carries three source FKs
- `resolved_at` nullable, `created_at`, `updated_at`

Uniqueness: `uq_stock_take_lines_take_material (stock_take_id, material_id)`, and for
products a unique **expression** index on
`(stock_take_id, product_id, COALESCE(variant_id, -1))` — both SQLite and Postgres treat
NULLs as distinct in a plain unique index, so a bare three-column constraint would let
`(take, product, NULL)` duplicate.

Phase B also adds `last_stock_take_id` (nullable FK → `stock_takes`, `ON DELETE SET NULL`)
to `materials`, `products` and `product_variants`.

### Lifecycle — `backend/app/services/stock_takes.py` (new)

**Start.** Resolve scope → candidate items → insert one line per item with
`expected_qty` = current system qty at that instant. Candidates exclude inactive items
and bundle products; a product with active variants contributes its variants, not
itself. **Soft lock:** any candidate already on a line of another `open` take is
included anyway, with a warning returned ("already in an open stock take started
<date>"). No hard block.

**Counting.** `PATCH .../lines/{line_id}` sets or clears `counted_qty` and `notes`;
`status` flips `pending` ⇄ `counted`. `PUT .../lines` does the same in bulk, and is what
the confirmed CSV import calls.

**Approve.** Per line:

```
counted_qty IS NULL          → skipped; no adjustment; last_stock_take_at NOT updated
current_qty != expected_qty  → conflict("moved since snapshot"); system_qty_at_approval
                               recorded; no adjustment; date NOT updated
allocated_qty > 0            → conflict("N units allocated to open orders — picked stock
  and counted < expected        may be boxed and not yet shipped"); no adjustment; date
                                NOT updated. See flag 3: this is the case that would
                                otherwise apply cleanly and silently write off real stock.
otherwise                    → apply via the existing set-adjustment service;
                               status=applied; adjustment id stored;
                               last_stock_take_at / last_stock_take_id updated
```

Order matters: the movement check runs first, so a line that both moved and has
allocations reports the movement, which is the more specific fact. The allocation check
reads `allocated_qty` off the same freshly-loaded owner row as the movement check, so it
needs no extra query.

A count that confirms the existing quantity still goes through `mode=set` and writes a
zero-delta row — the models document that case as intended, and it puts "counted and
confirmed on this date" in the stock history rather than leaving a silent gap.

**Transaction boundary — one fresh session per line.** This supersedes the
"catch the exception and carry on" wording this plan carried before Phase A.1, which does
not work. Per `docs/backlog.md`, once a flush error is rolled back the session is
unusable: every later statement on it raises `MissingGreenlet`, including a bare `SELECT`,
and an explicit rollback does not recover it. A single-session loop that caught a refusal
would therefore die on the *next* line rather than on the one that failed.

So approve iterates line ids and opens its own short-lived session per line from
`async_session_factory` — the same thing `listing_push._debounced_push` already does to
work outside a request's session. A poisoned session is then discarded by construction at
the end of its own iteration and cannot reach any other line. Sessions are opened and
committed one at a time, never overlapping, which also keeps SQLite's single-writer rule
satisfied; WAL means the request's own (read-only, idle) session doesn't block them.

Both `costing.create_adjustment` and `stock_adjustments.create_stock_adjustment` commit
internally, so mutate the line's status *before* calling the service and let that one
commit carry both — rather than adding a `commit=False` parameter to two heavily-used core
services. On the rare refusal the whole per-line transaction rolls back together, and a
second short session records the `conflict` and its reason.

**The pre-checks are what make that path nearly unreachable**, which is the real defence:
counts are validated `>= 0`, and a `set` resolves to the counted value, so a material can
never go negative and a product can only trip the allocated floor — which the allocation
rule above already catches in Python first. A refusal therefore means something changed
underneath us between check and write, which is exactly when discarding the session is the
right answer anyway.

`approve` is **idempotent** — it skips lines already `applied`/`accepted_system` — so an
approval interrupted part-way (or one that hit the fault) is fixed by running it again.

**Close.** `status = closed`, `closed_at = now()`, regardless of outstanding conflict
lines (decision 1).

**Resolve** a conflict line — same three actions whether the parent take is open or
closed:
- `accept_counted` → apply the counted value now (re-checking the floor), `status=applied`
- `accept_system` → discard the count, no adjustment, `status=accepted_system`
- `reset` → clear `counted_qty`. On an **open** take the line returns to `pending` and is
  re-enterable. On a **closed** take there is no sheet to re-enter it in, so it becomes
  `skipped` — the item keeps its old count date and stays overdue, which is the correct
  outcome for "leave it, I'll count it next time".

**Standing variances** = lines with `status = 'conflict'` whose parent take is `closed`.

### API

All on `routers/stock_takes.py`. **Literal segments declared before `/{stock_take_id}`** —
`routers/materials.py` has the same hazard and solves it the same way.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/stock-takes` | list: status, started_at, `open_days`, line counts by status |
| `GET` | `/stock-takes/overdue` | Phase A, unchanged |
| `GET` | `/stock-takes/unresolved-variances` | standing variances across closed takes |
| `POST` | `/stock-takes/preview-scope` | candidate count + soft-lock warnings; **writes nothing** |
| `POST` | `/stock-takes` | create + snapshot; returns take with lines and warnings |
| `GET` | `/stock-takes/{id}` | take + lines (name, unit, expected, counted, delta, status) |
| `DELETE` | `/stock-takes/{id}` | abandon an open take; no adjustments; `open` only |
| `PATCH` | `/stock-takes/{id}/lines/{line_id}` | set/clear one count + notes |
| `PUT` | `/stock-takes/{id}/lines` | bulk count entry |
| `POST` | `/stock-takes/{id}/lines/{line_id}/resolve` | `{action: accept_counted\|accept_system\|reset}` |
| `POST` | `/stock-takes/{id}/approve` | the algorithm above; returns per-line outcomes |
| `GET` | `/stock-takes/{id}/export` | CSV, `text/csv` + `Content-Disposition` |
| `POST` | `/stock-takes/{id}/import` | multipart; `dry_run`, `on_error` |

Extend `DashboardSummary` with `unresolved_variance_count` and `open_stock_take` (id +
`open_days`).

### CSV flow

**Export columns:** `line_id, item_type, item_id, name, variant, category, unit,
expected_qty, allocated_qty, counted_qty, notes` — the last two blank on a fresh sheet,
pre-filled if counting is already under way, so the file round-trips. `allocated_qty`
rides along read-only for the same reason it's on the screen: a printed sheet is exactly
where someone won't otherwise know that five of the twelve are boxed by the door. It is
ignored on import.

**Import** — one endpoint, two calls, mirroring `BulkBomAmendModal`'s preview→apply:

```
POST /stock-takes/{id}/import
  file: UploadFile
  dry_run: bool = True
  on_error: "skip" | "fail" = "skip"
```

- Decode `utf-8-sig` (Excel BOM), `csv.DictReader`, `enumerate(reader, start=2)` so row
  numbers match what the user sees in the spreadsheet.
- Per-row validation, each row independent: `line_id` exists on **this** take;
  `item_type`/`item_id` match that line (catches a misaligned or hand-edited file);
  `counted_qty` parses as a `Decimal`; `>= 0`; whole-number check via the existing
  `validate_qty_for_unit` for materials and an integer check for products.
- Blank `counted_qty` → row skipped, line untouched (the "no upload = no change" rule).
- Returns `{matched, failed: [{row, error}], applied: bool}`.
- `dry_run=True` writes **nothing** — this is what feeds the confirmation screen.
- `on_error="fail"` + any failure → nothing written, result reports it.
- `on_error="skip"` (default) → valid rows applied, failed rows left blank and reported.

Re-sending the file on the second call keeps this stateless — no staging table, no
trusting a client-supplied row list, and validation is deterministic so the confirmed
preview matches what gets applied.

### UI

New nav link **Stock Take** in `routes/__root.tsx` (a 7th item in a non-wrapping flex
row — check it still fits the 800×600 default window).

- **`routes/stock-takes/index.tsx`** — list of takes with an "open X days" indicator, plus
  the scope picker as an expand-in-place form (the materials list's "Add material"
  pattern, not a modal): materials/products checkboxes, `Set<MaterialCategory>` category
  checkboxes copied from `routes/materials/index.tsx:63`, product-category multi-select,
  "overdue only". Live candidate count and soft-lock warnings via `preview-scope`.
- **`routes/stock-takes/$stockTakeId.tsx`** — `?tab=count|review` via `validateSearch`, so
  tab switches are real navigations the unsaved-changes blocker can intercept (the
  convention in `routes/products/$productId.tsx:49`).
  - *Count sheet*: table with a counted-qty input per row, `step={wholeNumberStepFor(unit)}`
    and `onBlur={normalizeQtyForUnit}`. Buffered via `useEditableCopy` + `SaveButton`
    ("editor form" pattern). **Register a dirty path and add it to the allocation table
    in `hooks/useDirtyRegistry.tsx`** — that table is explicitly maintained. Rows with
    `allocated_qty_at_start > 0` show it next to the expected figure ("12 — 5 picked"),
    since that is the difference between a real variance and a shelf that only looks
    short. Wrap the table in `overflow-x-auto` (see flag 1).
  - *Review*: expected / counted / delta, variances in `text-red-600`, conflict lines in
    an amber alert strip (`rounded border border-amber-300 bg-amber-50 p-3`) with the
    three resolution buttons inline, blank-count rows labelled "unchanged, not re-dated".
  - *Approve*: `ConfirmDialog` summarising "N adjusted, M flagged, K skipped".
- **`StockTakeCsvPanel`** — `CsvImportExport` can't be reused as-is; it's one-shot with no
  confirmation step. Export via `downloadCsv`; import posts `dry_run=true`, then a
  `Modal` lists the failed rows with "Apply N valid rows" (default) / "Cancel and fix the
  file", the second choice re-posting with `dry_run=false`.
- **`routes/stock-takes/unresolved.tsx`** — standing variances across all closed takes,
  each resolvable inline with the same three actions. Surfaced as a Dashboard section
  and a count, so follow-up doesn't depend on remembering which take a line came from.

---

## Build order

**Phase A — shipped**
1. ✅ Migration: `product_categories` + `products.product_category_id`; `abc_class` /
   `stock_take_interval_days` / `last_stock_take_at` columns; `material_category_abc`,
   `product_category_abc`, `abc_tier_settings`; two `general_settings` columns.
2. ✅ `services/abc.py` — resolution + batch forms + `compute_due_for_count`.
3. ✅ `routers/product_categories.py` + schemas, via `_reference_crud.py`.
4. ✅ `/settings/stock-count-settings` and `/stock-takes/overdue`; `DashboardSummary` field.
5. ✅ Frontend: Product Categories reference table, `StockCountSettings` panel, detail-page
   fields, products-list type column/filter, Dashboard section.

**Phase A.1**
6. `set`-mode adjustments update `last_stock_take_at`, in `costing.create_adjustment` and
   `stock_adjustments.create_stock_adjustment`.
7. Data-only backfill migration from existing `set` adjustments, as its own revision.

**Phase B**
8. ✅ Migration: `stock_takes`, `stock_take_lines` (incl. `allocated_qty_at_start`),
   `last_stock_take_id` columns.
9. ✅ `services/stock_takes.py` — scope resolution + snapshot, count entry, approve,
   resolve. 25 tests; the silent-write-off case fails when the rule is removed.
10. ✅ Router + schemas; `DashboardSummary` additions.
11. ✅ CSV export, then import with `dry_run`. 16 tests.
12. **Frontend — written and working, not yet committed.** List + scope picker, count
    sheet, review + approve, `StockTakeCsvPanel`, unresolved variances, Dashboard strip
    and nav link. Typechecks, builds, 183 frontend tests pass, and the whole flow was
    driven in a real browser with no console errors: scope preview → start → count →
    save → approve → the allocated line flagged → resolve from the standing-variance
    view.

**Remaining to finish Phase B**
13. Commit the frontend, plus a `_qty` fix in `services/stock_takes.py`: conflict reasons
    were rendering Decimal scale verbatim ("counted 5.0000 against 10.0000 expected") in
    a sentence written for a person. `Decimal.normalize()` alone turns 10 into `1E+1`, so
    the helper formats integral values with `:f`.
14. Frontend tests for the two pieces with real logic behind them: the count sheet's
    dirty-state guard, and the CSV confirmation's skip-vs-fail branches. `fakeBackend.ts`
    stubs `uploadCsv`/`downloadCsv` as `notImplemented` and the panel bypasses them with
    its own multipart call, so it needs a fake for that path first.
15. Changelog entry, written for users. Commit this plan as `docs/plan-stock-take.md`.
16. Final pass: full suites, `alembic upgrade head` against a populated database, and a
    live walkthrough including a concurrent change mid-count (which should flag as
    movement, not apply).

---

## Verification

**Backend** — `cd backend && uv run pytest`. New suites using `app/tests/conftest.py`
(in-memory SQLite, FK enforcement on, the `pushes` fixture for asserting listing pushes).
Follow the dominant style: import the router handler or service and call it with a
session, rather than driving HTTP.

`test_abc_classification.py`
- Resolution precedence at all three levels, for materials **and** products.
- Interval precedence: per-item → tier override → code default.
- A variant inherits its product's class and interval.
- `compute_due_for_count`: never-counted sorts first; a just-counted item is absent;
  inactive items and bundles excluded.

`test_stock_take.py`
- Snapshot at start; a movement between start and approve produces a **conflict**, not an
  adjustment, and leaves `last_stock_take_at` untouched.
- Blank count → `skipped`, no adjustment, no date update. This is the rule most likely to
  regress.
- A confirming count → zero-delta `set` adjustment, `last_stock_take_at` **updated**.
- **The silent-write-off case:** 10 on hand, 5 allocated, counted as 5. Lands exactly on
  the floor so the adjustment service would accept it — assert it is a conflict and that
  `current_stock` is still 10 afterwards. This is the single most important assertion in
  the suite; without the rule it passes as a clean count and destroys stock.
- A counted value below `allocated_qty` → conflict with a reason, **not** a 400 and not a
  failed approval.
- A short count on a line with **no** allocation still auto-adjusts — the rule must not
  quietly turn every variance into manual review.
- A count at or above expected on an allocated line auto-adjusts.
- A material count that would go negative → same.
- Close with outstanding conflicts succeeds; the lines appear in unresolved-variances.
- All three resolutions, on an open take and on a closed one (`reset` → `pending` vs
  `skipped`).
- Approve is idempotent — running it twice applies nothing twice.
- **A refusal on one line does not stop the rest.** Force one (e.g. change `allocated_qty`
  underneath the pre-check so the service refuses) and assert every later line still
  applies. Without the session-per-line design this fails on the *following* line with
  MissingGreenlet rather than on the guilty one, which is what makes it worth an explicit
  test rather than trusting the structure.
- Soft lock: overlapping takes warn and proceed.
- A product with active variants yields variant lines, not a product line.

`test_stock_count_from_adjustments.py` (Phase A.1)
- A `set` adjustment on a material moves `last_stock_take_at` to now and drops it off the
  due list; an `adjust` adjustment leaves the date untouched.
- Same for a product and for a variant, since the owner is resolved not assumed.
- A `set` that confirms the existing quantity (zero delta) still updates the date — the
  count happened regardless of whether it changed anything.
- The backfill picks the **most recent** `set` per item, ignores `adjust` rows entirely,
  and leaves never-recounted items NULL. Worth running against a copy of the real
  database as well as fixtures, since it is the only step whose input is existing data.

`test_stock_take_csv.py`
- Export → import round-trip.
- `dry_run=true` writes nothing.
- `on_error="fail"` with one bad row writes nothing.
- `on_error="skip"` applies the good rows and leaves the bad lines blank.
- Blank `counted_qty` leaves the line untouched.
- `utf-8-sig` (Excel BOM) decodes.
- A row whose `item_id` doesn't match its `line_id` fails that row only.
- Fractional count on an `each` material fails that row only.

**Frontend** — `cd frontend && npm test`. Route tests via `src/test/fakeBackend.ts`.
Note: `uploadCsv`/`downloadCsv` are currently stubbed there as `notImplemented` — they
need real fakes before the CSV panel can be tested. Cover the count sheet's dirty-state
guard and the import confirmation's two branches. Also `npm run build` (tsc + vite),
which is what CI actually runs.

**Migrations** — the test suite builds schema with `Base.metadata.create_all`, not
Alembic, so migrations are **not** covered by the tests. Run `alembic upgrade head`
against a copy of a real `stocksmith.db` and confirm both new-table and
add-column migrations apply, including the SQLite table-rebuild path.

**Manual end-to-end** — `uv run uvicorn app.main:app --port 8000 --reload` plus
`npm run dev`:
1. Assign tiers, confirm the overdue list populates and sorts sensibly.
2. Start a take scoped to one material category; check expected quantities match.
3. Enter some counts, close the app, reopen — the draft is still there (this is the
   server-side persistence requirement, verified the only way it can be here).
4. Export CSV, edit it including one deliberately broken row, import; confirm the
   preview, apply the good rows.
5. In a second window, record a build or ship an order touching a counted item, then
   approve — that line must land in conflict, everything else applies.
6. Close with the conflict outstanding; confirm it shows on the Dashboard and the
   unresolved-variances view, and resolves from there.
7. Confirm `last_stock_take_at` moved only for counted-and-applied lines, and that the
   stock history shows the set adjustments.

**Repo hygiene** — write new files with LF endings (`docs/backlog.md` documents four
occurrences of tooling silently converting files to CRLF and destroying diffs). Add an
`## [Unreleased]` CHANGELOG entry written for users, per the note at the top of
`CHANGELOG.md`.

---

## Phase C — Grouping, made-to-order, and first-receipt counts

Shipped after Phases A and B, in the release that also introduced per-line receiving.
Recorded here because all three change what a count sheet contains, which is what the rest
of this document specifies.

**Product Type became Product Category.** The same idea already existed for materials under
the other name, and the two list pages are meant to read alike. Renamed throughout — table,
columns, routes, API client, labels — in `b6c9e1a4f273`. It is a rename migration rather
than an edit to `a7c3e1f09b52` because that revision had already run against a real
database by then; the shipped build carried it.

**Count sheets are grouped**, by `services/stock_takes.group_lines`:

```
Products                       Materials
  Product category               Material category   (enum order, not alphabetical)
    Parent SKU                     Material type
      Variant
```

Ordered there rather than by row id. Ids run in creation order and would have been free,
but they freeze the arrangement at the moment the take started, so recategorising a product
afterwards would leave it filed under its old heading for the rest of the take's life. All
three read paths — the count sheet, the CSV, the standing-variances list — go through the
one call, which is what stops them drifting into three different arrangements.

The CSV's `category` column is replaced by `section`, `group` and `subgroup`. One column
could only ever express one level of a three-level arrangement, and the CSV is the copy
that gets printed and carried around. Import is unaffected: it keys on `line_id` and
ignores them, so a re-sorted sheet still round-trips.

**`products.made_to_order`** excludes a product and all of its variants from scope
resolution and from `compute_due_for_count` — the same two places `is_bundle` is excluded,
for the same reason. The dashboard card reads through the second, so it needs nothing of
its own. Product-level deliberately: a shop stocking some colourways and building others on
demand has two products, not one product with two kinds of variant.

**A material's first delivery counts as a count.** Stamped in
`services/purchase_receipts.record_receipts` when the material's ledger is empty and
`last_stock_take_at` is NULL — which follows Phase A.1's principle of putting the stamp in
the service that knows what the event means. Deliberately narrower than "any delivery onto
a material at zero": a material that ran down through consumption has an *unverified* zero,
and that belief is exactly what a count exists to test. An empty ledger is a provable zero,
so the quantity after the delivery is exactly what was delivered. Reversing that delivery
takes the stamp back off, when nothing else could have set it.

### Flag 5 revisited

"On day one every item reads as never counted" still holds for products, and is still the
honest display. It no longer holds for materials bought in after the upgrade: their first
delivery dates them, so the never-counted list drains as stock arrives rather than needing
a counting session to clear it.
