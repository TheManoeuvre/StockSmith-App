# Plan — Storage locations

Written 2026-11-24 against 0.28.0. **Phases 1 and 2 are built** (2026-11-25): labels at 51 × 25 mm and
6 × 4 in, the print screen, Settings › Labels, scan-to-jump, and CSV import and export of the layout. The QR
code option was left out because nothing can use it yet. Count by location (phase 3) and the phase 4 hooks
are still to do.

StockSmith tracks *how much* of everything there is, but not *where it is*. This plan adds
**locations**: a free-form tree (`Office › Shelf 01 › Grey Box 03`, or just `Box A1`) that
materials and finished products can be stored in, with bulk creation, moves, printable
barcode labels, count-by-location, and automatic stock tracking (deducting and adding) for the common case of an
item that lives in one place.

---

## 1. Decisions recorded

| Question | Decision | Consequence |
|---|---|---|
| Quantity per location, or one location per item? | **Quantity per location** (2026-11-24) | An item can be split — 5 on the shelf, 20 in the loft. Stock not placed anywhere shows as **Unassigned**. |
| Do placements follow stock changes? | **Single-placement items follow stock automatically in both directions, from phase 1; multi-placement items prompt, pre-filled (phase 4)** (2026-11-24) | See §5. Stock going down is deducted from the one placement; stock going up (receipts, returns, reversals, manual increases) is added to it. Most stock lives in one spot with overflow elsewhere, so the system rarely has to guess. |
| Label output | **Label printer first; 51 × 25 mm default, 6 × 4 in also supported** (2026-11-24) | Single-label layout is the primary layout, with those two as built-in presets plus a custom size. An A4 sheet is a later extra. |
| Mandatory? | **No — but surfaced in onboarding, docs and the sample workspace** (2026-11-24) | An optional checklist step, a help page, and sample locations. See §9. |
| How many levels? | **Unlimited, no level names** | A tree with `parent_id`; "Office.Shelf_01.Grey_Box_03" and "Box_A1" are the same structure at different depths. No level-definition screen. |

### Out of scope (deliberately)

Capacity limits · per-location costing or FIFO lots · location "types" or named levels ·
multi-warehouse / multi-site stock transfers · choosing a destination when receiving into an item
that has no placement or several (put-away prompt, phase 4).

---

## 2. What already exists

| Need | Existing thing | Path |
|---|---|---|
| Barcode rendering and print layout | `BarcodeLabel`, `material-label` / `product-label` print routes | `frontend/src/components/common/BarcodeLabel.tsx`, `frontend/src/routes/*-label.*.tsx` |
| Scoped, persisted cycle count | Stock take: open/closed takes, lines with expected vs counted, approve → adjustments | `backend/app/models/stock_take.py`, `docs/plan-stock-take.md` |
| Commit a counted/adjusted quantity | `create_adjustment` (materials), `create_stock_adjustment` (products/variants) | `services/costing.py`, `services/stock_adjustments.py` |
| Master-detail page pattern | Settings lists | `frontend/src/components/settings/ListsMasterDetail.tsx` |
| CSV conventions | `csv_io.py`, `downloadCsv` / `uploadCsv` | `services/csv_io.py`, `frontend/src/api/client.ts` |
| Two-step preview → apply UI | `BulkBomAmendModal` | `frontend/src/components/products/BulkBomAmendModal.tsx` |
| Setup checklist | `setup_steps()` + `SetupChecklist` | `services/onboarding.py`, `frontend/src/components/onboarding/SetupChecklist.tsx` |
| Sample workspace | `build_sample()`, built through the app's own service functions | `services/sample_data.py` |
| Stock-changing code (starting list for §5) | `builds.py`, `allocation.py`, `order_parcels.py`, `returns.py`, `stock_adjustments.py`, `costing.py`, `kitting.py`, `box_check.py`, `material_merge.py` | `backend/app/services/` |

---

## 3. Data model

### `locations`

| Column | Notes |
|---|---|
| `id` | |
| `parent_id` | Self-FK, nullable (NULL = top level). `ON DELETE RESTRICT`. |
| `name` | Required. Unique among siblings (case-insensitive). |
| `code` | Short stable scan code, e.g. `LOC-000042`. Unique, **never changes** — printed labels survive renames and moves. |
| `notes` | Optional free text. |
| `archived_at` | A location that has ever held stock is archived, not deleted. Archiving requires it to be empty. |
| `sort_order` | Optional manual order among siblings; default is natural-sort by name (`Box2` before `Box10`). |

The full path (`Office › Shelf 01 › Grey Box 03`) is computed from the tree on read and not
stored, so a rename or move can never leave stale paths behind. Moving a location may not
create a cycle (reject moving a location under itself or its descendants).

### `stock_placements`

One row = "this much of this item is in this location".

| Column | Notes |
|---|---|
| `location_id` | FK `locations`. |
| `material_id` **or** `product_id` + optional `variant_id` | Same ownership shape as `StockAdjustment`: a product that has active variants is placed per variant. Exactly one owner; enforced by a CHECK. |
| `qty` | Numeric, ≥ 0. Whole numbers for items whose unit has 0 decimal places (reuse `validate_qty_for_unit`). Products are integers. |
| `is_primary` | At most one per item (partial unique index). The pick-from location. The first placement an item gets becomes primary. |

Unique on (`location_id`, item). **Unassigned = item total − sum of its placements.** It is never
stored.

### `placement_events`

Append-only log of everything that touches a placement: `place`, `move`, `remove`, `auto_deduct`,
`auto_add`, `count`. Columns: item owner, `from_location_id`, `to_location_id`, `qty`, `kind`,
`reason`, `created_at`, optional `stock_take_id`. It is both the audit trail ("where did it go?")
and the move history shown on a location or item. It is **not** read to compute any quantity.

### Invariants

- Placements never change `Material.current_qty` / `current_stock`. They sit **beside** the
  ledger, not in it.
- `sum(placements) ≤ item total` is the healthy state. The reverse is **drift** (§5) and is
  surfaced, never silently "fixed".

---

## 4. Behaviour

### 4.1 Creating locations

- **Add one:** name + parent (blank = top level).
- **Generate a range:** one dialog, a pattern with tokens, a parent, a live preview.

| Pattern | Result |
|---|---|
| `Box{01-05}` | Box01 … Box05 (zero padding follows the start value's width) |
| `Bin-{A-F}` | Bin-A … Bin-F |
| `Shelf {1-3}` | Shelf 1 … Shelf 3 |
| `{A-D}{1-6}` | A1 … D6 — 24 locations (two ranges = a grid) |
| `Col{AA-AD}` | Col AA … Col AD (alpha ranges roll over A–Z, then AA, AB…) |

  Rules: at most one or two `{…}` tokens; hard cap of **500 per run**; end ≥ start; names that
  already exist under that parent are skipped and shown as such in the preview; nothing is
  written until the user confirms the preview. Pure function, unit-tested, shared between the
  preview and the create call so they cannot disagree.
- **CSV import / export** (phase 2): columns `path`, `notes`; parents are created as needed.

### 4.2 Placing and moving stock

- **Place:** item + location + qty. Cannot exceed the item's Unassigned quantity (you can't
  place stock that is already placed or doesn't exist). Placing is how Unassigned shrinks.
- **Move by item:** item, from, to, qty. Merges into an existing placement at the destination.
- **Move by location:** two different operations, kept visibly separate:
  1. **Move the location** (re-parent) — "move Box01 from Shelf 1 to Shelf 2". Contents travel
     automatically because placements point at the location, not the path. One cheap write.
  2. **Empty into another location** — "move everything in Box01 into Box02". Merges every
     placement; leaves Box01 empty.
- Every move writes `placement_events`. Moves never change item totals.

### 4.3 Finding things

Search by item name/SKU (where is it?) and by location name or scanned code (what's in it?).
Lists get a **Location** column (primary location, "+2 more" if split) and filters: *Unplaced*,
*In location…*. Locations page has an *Empty locations* filter.

---

## 5. Automatic deduction and adding

The aim: for an item that lives in one place, the location quantity stays right with no effort.

### 5.1 Rules (phase 1)

| Item's placements | Stock goes **down** (build, sale, scrap, adjustment) | Stock goes **up** (purchase receipt, returned or reversed build/order, manual increase, finished build) |
|---|---|---|
| None | Nothing to do | Nothing to do — stays Unassigned |
| **Exactly one** | Deduct `min(decrease, placement.qty)` from it. Record `auto_deduct`. | Add the full increase to it. Record `auto_add`. |
| **Two or more** | Nothing automatic. Drift warning if placed > total. | Nothing automatic. Stays Unassigned. |

- **Deductions clamp at the placement's quantity.** If a build needs 6 and the one placement
  holds 4, it takes 4; the other 2 were Unassigned stock. It can never go negative.
- **Increases add in full.** The observer sees only the change in the total, not why it
  changed, so it cannot tell a purchase receipt from the undo of a build. The cost: if a
  deduction was clamped and later reversed, the placement ends up slightly higher than it was
  (a build that clamped 6 → 4, undone, adds back 6). This is bounded — it can never exceed the
  item total — rare, and fixed by a count. The alternative (tracking what was auto-deducted to
  cap restores) was rejected because it would make receipts not auto-add, which is the
  behaviour wanted.
- **The first placement is what switches this on.** An item with no placement is left alone, so
  new stock for an item you haven't located never lands anywhere by surprise. Once it has
  exactly one, everything follows it.
- **Receiving into a multi-placement or unplaced item** leaves the new stock Unassigned in
  phase 1, with a "Put away" prompt on the item and on the receipt. The destination chooser
  and a per-item default location come in phase 4 (§5.2).
- The prompt-for-multi-placement behaviour (§5.2) is phase 4. Until then, multi-placement items
  rely on the drift warning and counts.

### 5.2 Multi-placement items (phase 4)

- **Someone is at the screen** (build, scrap, manual adjustment): an inline
  "Taking from: [Shelf 1 ▾]" field, **pre-filled with the last location used that still holds
  enough**, falling back to primary. Shown only for multi-placement items. Not a blocking dialog.
- **No one is there** (marketplace order sync, shipping): take from primary first, then overflow
  in order of quantity, clamped as above.
- **Replenishment nudge:** when primary falls to zero (or its reorder level, if set later) and
  overflow holds stock — "Shelf 1 is empty; move 4 from the loft?" — reusing move-by-item.

### 5.3 Where it hooks in — one choke point, not nine

Stock is changed in many places (`builds.py`, `allocation.py`, `order_parcels.py`,
`returns.py`, `stock_adjustments.py`, `costing.py`, `kitting.py`, `box_check.py`,
`material_merge.py`, CSV import). Patching each call site means a missed or future one drifts
silently. Instead, **observe the quantity itself**:

- **Materials:** `recompute_material()` is already the single place `current_qty` is derived
  (including after edits to historical purchases and builds). Capture the old value, and after
  recompute apply the **delta** under §5.1.
- **Products and variants:** `current_stock` is mutated directly in several places. A SQLAlchemy
  `before_flush` listener inspects `current_stock` attribute history on `Product` and
  `ProductVariant` and applies the delta. One listener, catches future code too.
- Both feed one function, `apply_stock_delta(owner, delta, reason)`, in a new
  `services/placements.py`. It is the only code that writes `auto_deduct` / `auto_add`.
- **Why delta-based:** it needs no knowledge of *why* stock changed, and it handles edits to
  history, which no per-event hook can.
- **Stop-and-check before building:** confirm the `before_flush` approach against how the
  allocation and shipping paths mutate `current_stock` (some are in loops with `await`). If it
  proves fragile, fall back to routing those writes through a small `change_stock()` helper.
  The rules in §5.1 do not change either way.

### 5.4 Drift

Placed > total can still happen: a multi-placement item consumed (until phase 4), or a past
record edited so the total fell with no event. Show it, don't hide it:

- On the item: *"Placed 5, but only 3 in stock"* with a one-click **Reduce Shelf 1 by 2**.
- On the Locations page: a count badge of items with drift; a *Needs attention* filter.
- A cycle count of the location fixes it permanently (§7).

---

## 6. Labels

- **Content:** full path in large text, the location name, and a **Code 128** barcode of the
  `code`. Optional QR of the same code (phase 2, only if a real use for phone scanning appears —
  there is no networked access today, so it is a straight alias for the same string).
- **Primary layout: single label at a configurable size**, printed from the browser print
  dialog with an `@page { size: Wmm Hmm; margin: 0 }` rule. Settings → new "Labels" setting,
  stored in mm, with two built-in presets and a custom option:

  | Preset | Size | Notes |
  |---|---|---|
  | **Small (default)** | 51 × 25 mm (≈ 2 × 1 in), landscape | Tight: leaf name large, parent path small on one or two lines, truncated with an ellipsis rather than shrinking text below a legible minimum; barcode fills the remaining height. |
  | **Shipping label** | 6 × 4 in (152.4 × 101.6 mm), landscape | Common thermal size. Roomy: full path in large text, large barcode, and the human-readable code underneath. Suits shelf and bay signs as much as boxes. |
  | Custom | width × height in mm | For other rolls. |

  Design needs both layouts: they differ in hierarchy, not just scale. Check that Code 128
  stays scannable at 51 mm wide for the longest code (quiet zones, minimum bar width — the code
  length is fixed at `LOC-` + 6 digits, so this is a one-off check, not a per-label risk).
- **Selection:** tick several locations → one print job, one label per page. "Print labels for
  this range" straight after generating a range is the main path.
- **Later:** an A4 sheet grid (e.g. 3×8) as a second layout.
- **Scanning:** USB/Bluetooth scanners behave as keyboards. A global "scan or search" box
  accepts a `LOC-…` code and jumps to the location. No camera or special API.
- Reuse `BarcodeLabel`; extend rather than fork. The existing material/product label routes
  stay as they are.

---

## 7. Count by location (cycle counting) — phase 3

Extends Stock Take with a **By location** scope. Pick a location; its subtree is included.

- The count sheet lists, for each placement in scope, expected = the placement's `qty`, sorted
  by location so the counter walks the shelves in order. Scanning a location label opens that
  location's sheet.
- **A count is a statement about one placement, not the item total.** An item in three places
  with only one counted can't have its total "set". So committing applies the **delta against
  the placement** — counted − expected — through the existing adjust path
  (`create_adjustment` / `create_stock_adjustment`, mode `adjust`), with reason
  *"Stock take #n at Shelf 01"*, and sets the placement to the counted quantity. Item totals move
  by the same delta.
- Items **not on the sheet** but physically found: "add item" on the count sheet places and
  adjusts in one step.
- Updates `last_stock_take_at` per item as today (a count of the only placement is a full
  count; a partial count of a multi-placement item does **not** date the item — reuse the
  existing "leaving it blank asserts nothing" principle).
- Existing item-scope stock takes are unchanged.

> This is the fiddliest phase because it is where the placement ledger and the stock ledger meet
> in both directions. It gets its own plan section before it is built.

---

## 8. Where it lives in the app

- **New nav item "Locations"** in the **Stock** group, between Purchases and Stock Take.
  Route `/locations`.
- **Locations page — master-detail.**
  - Left: the tree, with expand/collapse, item counts, and search.
  - Right (selected location): breadcrumb path, contents (items + qty), actions
    *Add child · Generate range · Move location · Empty into… · Print label · Count this
    location*, and its movement history.
  - Header: *Needs attention* (drift) and *Unplaced stock* shortcuts.
- **On items:** a **Stored in** section on the material detail and the product/variant
  `StockSection`: placements, Unassigned remainder, drift warning, Place / Move actions.
- **Lists:** Location column + filters on Materials and Products.
- **Order page** (phase 4): the pick location next to each line; pick list sorted by location.
- **Settings:** label size only. Creating and managing locations is not a setting.
- **Dashboard:** nothing in v1 beyond the checklist step.

---

## 9. Onboarding, documentation and sample data

Locations are optional, but they're a feature that gets used only if people discover them, so
they are included in every onboarding surface. (CLAUDE.md: every feature considers this.)

### 9.1 Setup checklist

- New optional step **`locations`** in `services/onboarding.py`: done when at least one location
  exists. Placed after `opening_stock` — you need something to put somewhere, and storage is
  the natural next step once stock is counted. It's optional, so it doesn't hold the checklist
  open once dismissed, and it shows until done like the other optional steps.
- Optional **second-level nudge** (not a separate step): once locations exist and stock exists
  but nothing is placed, the Locations page shows "Place your stock" as its empty state.
- Frontend: title, wording and deep link to `/locations` in `SetupChecklist.tsx`; update its
  test and `test_onboarding.py`.
- Update `docs/plan-onboarding.md` §3 (page-by-page table gets a Locations row) and §5.

### 9.2 Empty states and intro

- **Locations, empty:** "Where do you keep things?" — one sentence on how locations nest, a
  primary **Add a location**, and **Generate a range** with a one-line example (`Box{01-05}`).
- Page intro (2–3 sentences, dismissable, reopened by the "?" in the header) + "Learn more" →
  help page.
- Item **Stored in** section, when nothing placed: "Not stored anywhere yet" + Place action.

### 9.3 Help documentation (`docs/help/`)

- **New page `docs/help/locations.md`:** what locations are, nesting, generating a range
  (pattern examples), placing and moving stock, what Unassigned means, how automatic deduction and adding
  work (single placement vs several), drift, printing labels, counting by location. Add the
  file, its slug in `HELP_SECTIONS` (`frontend/src/lib/help.ts`) and a link from
  `docs/help/README.md` under **Stock** — `help.test.ts` fails otherwise.
- Update **`getting-started.md`** (new optional step in the order of setup), **`concepts.md`**
  (add Location as a concept), and the Stock Take help (the "by location" scope).
- Use `<HelpLink doc="locations" anchor="…">` from the page intro and the range dialog.

### 9.4 Sample workspace (`services/sample_data.py`)

The candle-maker demo should show locations working, built through the real service functions
like the rest of the sample so derived figures are genuine:

- A small tree: `Workshop › Wax shelf`, `Workshop › Oils rack › Bin 1–3` (made with the range
  generator), `Workshop › Jar store`, `Stockroom › Shelf A…`, plus an overflow `Loft`.
- Place most materials in a **single** location, each with its opening quantity, so the demo's
  builds and shipped orders visibly **auto-deduct**, and the sample's incoming purchase, once received, **auto-adds**
  (the headline behaviour).
- Give one or two items an **overflow placement** (e.g. jars: some in the Jar store, some in
  the Loft) to show a split and the Unassigned remainder.
- Place finished products so the product side of auto-deduct is visible after sample orders ship.
- Leave one material **unplaced** so the "Unplaced" filter and the empty-ish state have
  something to show.
- A `test_sample_data` assertion that the sample has locations and that no placement exceeds
  its item total.

### 9.5 Other

- `CHANGELOG.md` entry under *Unreleased → Added*, written for users.
- No seed data on a real install (locations are business-specific); `seed.py` unchanged.
- Presets (`business_presets.py`) unchanged; location names are not business-type-specific.
- Nothing UK-specific. Location names and label sizes are user data.

---

## 10. Phasing

### Phase 1 — Foundation, with auto-deduct for single placements
- Migration: `locations`, `stock_placements`, `placement_events` (SQLite- and Postgres-portable,
  `portable_enum` for `kind`).
- `services/locations.py` (tree ops, cycle check, path rendering, range-pattern parser) and
  `services/placements.py` (place, move, `apply_stock_delta`).
- Routers/schemas; `before_flush` + `recompute_material` hook (§5.3).
- Locations page (tree, detail, add, generate range, move location, empty into…), item
  **Stored in** sections, Location column and filters, drift warning and fix.
- **Onboarding checklist step, empty states, help page, sample data** (§9) — shipped with
  phase 1, not later.
- Tests: range parser (padding, alpha rollover, grids, cap, collisions), cycle rejection,
  sibling-name uniqueness, Unassigned arithmetic, auto-deduct clamping, auto-add on receipt and on reversal, edit of
  a historical record, multi-placement does *not* auto-deduct, and a "no code path bypasses the
  hook" test per stock-changing service.

### Phase 2 — Labels, scan and CSV
- Label size setting, single-label print route, multi-select printing, print-after-generate.
- Global scan/search box. Locations CSV import/export.

### Phase 3 — Count by location
- "By location" stock-take scope, delta-against-placement commit, add-found-item. Own design
  note first (§7).

### Phase 4 — Workflow hooks
- Multi-placement prompt and automatic fallback (§5.2), replenishment nudge.
- Put-away destination chooser on purchase receipt for items that are unplaced or in several
  places, with a per-item default location pre-filled. (Receiving into a single-placement item
  already auto-adds from phase 1.)
- Put-away after builds, pick location on orders and a location-sorted pick list.
- A4 label sheet.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Delta hook misses a mutation path, so placements drift quietly | One observer rather than per-site patches; per-service tests; drift warning is the safety net |
| `before_flush` listener interacts badly with allocation loops | Spike first (§5.3); `change_stock()` helper as fallback |
| Users expect receipts to auto-place for unplaced or split items | Documented; shown as Unassigned with a "Put away" prompt; destination chooser in phase 4. Single-placement items already auto-add |
| Auto-add puts stock somewhere the user didn't intend (e.g. a delivery that is going to the loft) | Visible in the item's movement history; one-click move; documented. Only fires for items with exactly one placement |
| Range generator creates hundreds of unwanted rows | Preview before write, 500 cap, skip-existing, and archive/delete-if-empty to undo |
| Count-by-location semantics confuse totals | Delta model, own design note, tests on partial counts |
| Printed labels invalidated by renames | Scan code is separate from the name and immutable |

---

## 12. Open questions

Resolved 2026-11-24: label sizes (51 × 25 mm default, 6 × 4 in supported — §6) and auto-add on
increase for single-placement items (§5.1).

1. **Does the printer's driver honour custom page sizes from the browser print dialog?** Browser
   printing via `@page` assumes it does; some thermal printers need the driver's paper size set
   to match. Test a real print of both presets early in phase 2, before the layouts are
   finalised.
2. **Depth limit?** Currently none. A soft warning past ~8 levels may be kinder than a cap.
3. **Do bundles' components get placed, or only the bundle?** Bundles derive from components'
   stock, so assume components only; confirm when the product side is built.
