# StockSmith — Work flow: routes, "next action" list & stock top-ups

**Status:** Planned 2026-11-27. Nothing built. Sequencing: starts only after PR #22 and PR #23
land (see §9) — it adds columns to `products` and a section to the dashboard, both of which the
roadmap fences off while those are in flight.

## 1. Context

StockSmith knows what *exists* (stock, orders, builds) but not what *to do next*. Between "an
order arrived" and "it shipped" there is real work that varies by product — cleaning, masking,
sanding, making a personalised file, printing, gluing, packing — and the person doing it has to
hold the whole picture in their head and decide, every few minutes, what comes next. For a
seller doing ~280 orders a month at peak, with several product types and three printers, that
decision-making is the load, not the work itself.

`docs/roadmap.md` lists print-queue management as "never started, never planned". This is
that plan, widened: not just a queue for the printers, but the whole path from order to
shipped, plus keeping popular items topped up when there is downtime.

The seller we are designing from (the author, a UK Etsy seller — flagged because the app is
for sellers anywhere): Etsy-led, 3 printers, 2–3 hour prints, batches by product and colour,
overnight runs, a ship-by date that is a promise. Four routes today:

| Product type | Steps |
|---|---|
| Standard print | Print → (assemble) → ship |
| Personalised | **Make file** → print → (assemble) → ship |
| Ceramic coaster | Pre-clean → mask → print → assemble → ship |
| Wood coaster | Pre-treat → sand → clean → mask → print → assemble → ship |

**None of that may be hard-coded.** The app ships generic building blocks; the seller defines
their own steps and routes.

## 2. Decisions recorded (from the author, 2026-11-27)

1. **Configurable, not hard-coded.** Stages and routes are user data.
2. **A route attaches at product category, with a per-product deviation** — the same shape as
   ABC classification (`ProductCategoryABC` + `Product.abc_class`).
3. **Bulk ticking, or setting a number.** Pack sizes are variants and need proportionally more
   prep: a 6-pack needs six coasters prepped, a 2-pack needs two. Progress is entered as a
   quantity, not a checkbox per order.
4. **Build-for-stock goes through the same flow** as build-for-order.
5. **Stock targets.** Today there is "amount we can build" but no "on-hand level". Add an
   optional "aim to keep X on hand", or a **recommendation derived from recent sales** (e.g.
   4–5 a day for 30 days → "when there's downtime, run a batch of 6").
6. **Generic anchor naming.** The built-in production step is called **Make** (or Assemble),
   not "Print". StockSmith is not 3D-printing-specific.
7. **Machine integration (Bambuddy, "what's printing now") is out of scope here.** It needs an
   API / add-on framework, which is a bigger piece. This plan leaves the door open (§7) and
   names it as a separate follow-on plan.

## 3. What the codebase already gives us

Reuse these; every one was read in the source.

| Need | Existing thing | Where |
|---|---|---|
| Category → item override with NULL = inherit | `ProductCategoryABC`, `Product.abc_class`, three-level fallback in one module | `models/abc_classification.py`, `services/abc.py` |
| Product grouping | `ProductCategory` (+ `Product.product_category_id`) | `models/product_category.py` |
| Reference-table CRUD (rename, merge, delete-if-unused) | `_reference_crud.py`, `services/reference_data.py` | `routers/_reference_crud.py` |
| Record a finished build; failed prints; material consumption | `Build`, `BuildFailedConsumption`, `services/builds.py` | `models/build.py` |
| Resolved BOM incl. variant overrides (what a 6-pack consumes) | `get_resolved_variant_bom` | `services/buildability.py` |
| "Amount we can build" | buildability service | `services/buildability.py` |
| Order due date | `Order.ship_by_date`; `ordered_qty` / `allocated_qty` / `shipped_qty` per `OrderLine` | `models/order.py` |
| Orders short on stock | dashboard summary | `schemas/dashboard.py`, `routers/dashboard.py` |
| Sales rate per product/variant | forecasting's sales-history aggregation (30-day style lookbacks, business-day helpers) | `services/forecasting.py` |
| Append-only ledger convention | `ProductStockEvent`, `StockAdjustment` | `models/product_stock_event.py` |
| Made-to-order exclusion | `Product.made_to_order` | `models/product.py` |
| Sparse settings (code defaults win, table ships empty) | `ABCTierSetting` | `models/abc_classification.py` |
| Onboarding checklist, sample data, presets, help system | `services/onboarding.py`, `sample_data.py`, `business_presets.py`, `docs/help/` | see `docs/plan-onboarding.md` |

## 4. Concepts

### 4.1 Stage
A named step with: a **name**, a **station** (a free-text grouping the seller defines — "Desk",
"Bench", "Printers", "Pack" are the suggested starters; stations exist only to group the work
list so the seller isn't switching places), an optional **wait** (drying/curing time before the
next stage can start), and an optional **typical time** (used only for hints, §6.4).

Two stages are **built in and not deletable**, because they connect to logic that already exists:

- **Make** — the step that produces the item. Completing it records a `Build` through the
  existing build service (materials consumed, stock increased, failures captured). Renameable;
  default label "Make".
- **Ship** — satisfied by the existing order flow (label/handed over → `shipped_qty`). Never
  ticked by hand.

Everything else is the seller's. A fresh install has only Make → Ship.

### 4.2 Route
An ordered list of stages. Routes are a small reference table. **Resolution** mirrors ABC:

```
route = product.route_id ?? product_category.route_id ?? shop default route (Make → Ship)
```

Variants do **not** get their own routes in v1. Pack sizes differ in *quantity*, not in steps
(§4.4), so a per-variant route would be configuration with no job to do.

### 4.3 Demand and progress
- A **demand** is "N units of product/variant V are needed". It comes from either an
  **order line's shortfall** (ordered − allocated − already shipped; items already in stock
  skip straight to Ship) or a **top-up** (§5).
- **Progress** is an append-only **event ledger**: `(demand, stage, qty, at, note)`. A stage's
  done quantity is the sum; an undo is a negative event. This follows the codebase's ledger
  convention and gives timestamps for free (needed for wait times).
- A stage's **available** quantity = done at the previous stage (and past its wait) − done at
  this stage. **The next-action list is that, grouped and sorted** — nothing is stored beyond
  events.

Progress attaches to the **demand**, not to a pool of generic WIP. That costs a little more
than a pooled count but means personalised items (a file made *for this order*) and ordinary
items behave identically. A bulk tick ("masked 12") is allocated to demands in **ship-by
order**, earliest first, and the seller can override.

### 4.4 Quantity per stage (the pack-size rule)
Each stage has a **quantity basis**:

- **Per finished unit** (default): a 6-pack variant ordered ×1 = 1 unit.
- **Per unit of a chosen BOM material**: "per ceramic blank in the BOM". A 6-pack's resolved
  BOM holds six blanks, so pre-clean and mask show **6**; a 2-pack shows **2**. This reuses
  `get_resolved_variant_bom`, so variant overrides already do the right thing.

Recommendation: ship both. The default needs no configuration; the BOM basis is what makes
multi-packs correct.

## 5. Stock targets and top-ups

Today: `current_stock`, `allocated_qty`, and buildability. New, on the item that holds stock
(a variant if it has active variants, else the product — same scoping rule `services/abc.py`
uses; bundles excluded; `made_to_order` excluded):

- `target_on_hand` — nullable int. "Aim to keep X."
- `target_mode` — `none` | `fixed` | `suggested`. NULL inherits a shop-wide default
  (initially `none`, so nothing changes for anyone who doesn't opt in).
- `batch_size` — nullable int. The natural run (e.g. 6 on a plate; 3 or 8 for overnight).

**Suggested target** = sales per day over a lookback × cover days, using forecasting's existing
sales aggregation. Both numbers are shop settings (initially 30-day lookback, 3 days cover).
**Top-up quantity** = target − (on hand − allocated + already in progress), rounded **up** to
`batch_size`. It appears in the work list as a low-priority "top up" demand for use when
there's downtime; it never outranks an order. The seller accepts it (creating a top-up demand
that flows through the same route) or dismisses it.

The suggestion is explained on screen — "sold 4.6/day over 30 days; 2 on hand; batch of 6" —
because a number the seller can't audit is a number they'll ignore.

## 6. The work list ("Next actions")

A new page, **Work**, plus a dashboard tile (late / due today).

### 6.1 Layout
Grouped by **station**, then within a station sorted by ship-by date, then **batched**:
cards for the same product (and the same colour — see §10) are merged into one batch with a
total quantity. Each card shows stage, product/variant, quantity, earliest due date, and the
**Done** control.

### 6.2 Done control
A number field defaulting to the card's full quantity, with a single click to confirm. Entering
less records partial progress. Make-stage cards open the existing build form pre-filled
(including failed qty).

### 6.3 Where does "what next" come from?
A top banner shows the single recommended next action: the highest-urgency available card at
the seller's current station. Switching station changes the list. This is the direct answer to
"what do I do next" without opening the whole business.

### 6.4 Later refinements (phase 4)
- **Pull-forward rule:** batch an item only if no order becomes late; items with slack join a
  running batch.
- **Wait-time gating:** after a stage with a wait, the next stage isn't actionable until it
  elapses.
- **Typical times** → "you can fit three of these before the 2pm cut-off" hints.
- Orders with no `ship_by_date` (manual) fall back to placed-date plus a handling-days setting.

## 7. Leaving the door open for machines and integrations

Not built here. Three things we do now so it isn't a rewrite later:

1. Progress events carry optional `source` and `external_ref` columns (null for manual).
2. The next-action derivation is a **service function behind the API**, not logic in the UI —
   an integration will want to read and write the same thing.
3. Nothing is named "printer". A later **resource** concept (a machine with capacity and a
   current job) can sit beside stages without renaming anything.

**Follow-on plan, not started:** an API / add-on framework and a Bambuddy integration. I have
not researched Bambuddy's interface; that is the first task of that plan.

## 8. Onboarding & docs impact (per `CLAUDE.md`)

- **Seed / defaults.** One shop default route (Make → Ship) and nothing else — the feature is
  **off until enabled** (a Settings toggle), so existing installs and fresh installs both see
  an unchanged app until they opt in. Optional example routes could be offered per business
  type in `business_presets.py`; they're sample content, not seed.
- **Setup order.** Routes need product categories, which already exist in the setup order. Not
  a required checklist step; offered as an optional one once the seller has orders.
- **Empty states.** The Work page needs one that says what to do ("No work waiting — turn on
  routes in Settings → Workflow, or nothing is short on stock").
- **Sample workspace.** Give the sample shop a route using its own words (Ember & Fig Candle
  Co. → a pour/cure route exercises the wait time well).
- **Help.** New `docs/help/workflow.md`, its slug in `HELP_SECTIONS`, a link from
  `docs/help/README.md`; update `concepts.md` and `builds-and-failures.md`. `<HelpLink>` from
  Settings → Workflow and the Work page.
- **UK-specific flags.** None in the model. Two things to keep locale-neutral: business days
  (Mon–Fri is assumed by the existing forecasting helpers — make the working week a setting if
  it isn't already) and the handling-days default. Stage and station names are the seller's
  own words, so there is nothing to translate.

## 9. Build order

Each phase ships on its own and leaves the app consistent.

1. **Stages & routes (config only).** Stage and route tables, built-in Make/Ship, category and
   product assignment, shop default, the enable toggle, Settings → Workflow UI, help page,
   sample route. Route shown read-only on products and order lines.
2. **Work list.** Demand derivation from order shortfalls, event ledger, bulk tick, quantity
   basis, station grouping, batch-by-product, Make linked to the build form, Work page,
   dashboard tile, tests.
3. **Stock targets & top-ups.** Fields, shop settings, suggestion with its explanation, accept
   / dismiss, top-up demands through the same route.
4. **Batching rules & timing.** Pull-forward rule, wait gating, typical times, handling-days
   fallback.
5. **Separate plan:** API / add-on framework; Bambuddy.

**Gate:** phases 1–3 touch `products` and the dashboard schema, which the roadmap holds until
PR #22 and PR #23 merge. Planning and review can proceed now.

## 10. Open questions — need an answer before phase 1

1. **"Make" vs "Assemble" for the anchor.** Decision 6 says the default production step is
   assemble-or-make. Is **Make** (the step that records a build) the right single anchor, with
   "assemble" as an ordinary seller-defined stage after it? That is how this plan reads it.
2. **Colour as a batch key.** Batching "same product, same colour" needs the app to know the
   colour. Is it a variant attribute, or only visible through the filament in the BOM? If the
   latter, the batch key is derived from the resolved BOM's colour, which is workable but
   should be confirmed against real products.
3. **Leftover work when demand disappears** (order cancelled after masking). Plan: the finished
   prep is simply credited to the next demand for that product at that stage (so it isn't
   wasted), but this is the weakest part of attaching progress to demands rather than to a
   pool — check it against how often cancellations really happen.
4. **Multi-item orders.** An order ships when its last line is ready. Is waiting for the whole
   order the usual case, or do you partly ship? (Existing `shipped_qty` handles partials; the
   question is what the Work list should *encourage*.)
5. **Per-category target defaults.** Fixed/suggested mode inherits item → shop. Do you also want
   a category level, as ABC has? Cheap to add, and more to configure.
