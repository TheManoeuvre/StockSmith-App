# Plan — Personalised / made-to-order products: allocation, builds and listing quantity

Status: **draft for review** (2026-11-24). Nothing here is built. Section 7 lists decisions still needed.

## 1. Problem

Some products (coasters, nameplates) are always customised with text or images. Once built,
a unit belongs to one specific order. StockSmith doesn't model that:

1. **Allocation ignores personalisation.** `services/allocation.py` allocates first-in-first-out
   by product/variant. `OrderLine.variation_text` (the buyer's text/artwork) is stored but never
   consulted. A finished "Pippa" coaster sits in free stock and can be allocated to "Dave"'s order,
   either at order time (`allocate_order`) or after a build (`auto_allocate_after_build`).
2. **The `made_to_order` flag does very little.** `Product.made_to_order` only excludes a product
   from stock takes and ABC classification.
3. **The pushed listing quantity can oversell made-to-order products.** Confirmed with a probe
   test (2026-11-24): materials for 10 units, no finished stock, 8 open unbuilt orders → the
   quantity pushed to the marketplace stayed at **10**, not 2. Build materials are only
   deducted when a build is recorded. `Material.allocated_qty` is a reservation, but only for
   packaging (kitting) materials, not build materials. Etsy lowers its own quantity on a sale,
   then the next StockSmith push resets it. This affects every made-to-order product today.

## 2. Approach

Two rules that work together:

- **Rule A — no free pool.** A personalised product is never allocated automatically. Its finished
  units can only be assigned through a build tied to one order line.
- **Rule B — build against an order line.** Starting a build for a personalised product requires
  choosing the target order line. Finished units are reserved to that line only.

Plus a separate fix that stands on its own:

- **Rule C — reserve build materials for open unbuilt demand** on made-to-order products, so the
  pushed quantity is `materials on hand − materials owed to open order lines`.

## 3. Scope by phase

### Phase 1 — Stop the wrong-item risk and the overselling (smallest safe change)

1. **Flag.** Mark personalised products (see decision 7.1: reuse `made_to_order` or add
   `is_personalised`).
2. **Allocation engine** (`services/allocation.py`):
   - `_allocate_line` and `auto_allocate_after_build` skip lines for personalised products.
   - Personalised order lines stay `pending` until built-against.
   - A manual allocate endpoint on such a line is refused with a clear message.
3. **Materials reservation** (`services/buildability.py`, `listing_push._resolve_max_sellable`):
   for made-to-order products, subtract materials needed by open, unbuilt order lines
   (`ordered_qty − allocated_qty − short_shipped_qty`, excluding cancelled, shipped and
   `needs_mapping` lines and orders with `marketplace_shipped_at`) from `current_qty` before
   computing buildable units. Must pool correctly when several products share a material.
4. **Listing push.** For personalised products always push the materials-based figure, never
   finished stock, and trigger a re-push when an order arrives or is cancelled.
5. **Cancellation/return.** A personalised unit built for a cancelled order or returned by a buyer
   is scrapped, not released to free stock (`services/returns.py`, cancel flow).

### Phase 2 — Build against an order line

1. **Data.** `Build` (or its allocation event) gets a nullable `order_line_id`. Personalised
   builds require it; other builds are unchanged. Needs an Alembic migration.
2. **Build flow** (`services/builds.py`): when the product is personalised, `create_build`
   requires `order_line_id`, caps `qty_built` at the line's outstanding qty, and allocates the
   result directly to that line instead of calling FIFO `auto_allocate_after_build`.
3. **UI.** The build form shows the order line's personalisation text and artwork links.
   The Dashboard "Build now" shortcut passes the order line through. The queue of
   orders to make is sorted by ship-by date (the existing `due_date` ordering).
4. **Failed builds.** A failure against an order line is costed to that order and leaves the
   line unallocated, ready to remake.

### Phase 3 — Optional

- Proof/approval status on the order line before building.
- A printable job sheet per build.

## 4. Files likely touched

Backend: `services/allocation.py`, `services/builds.py`, `services/buildability.py`,
`services/listing_push.py`, `services/returns.py`, `models/product.py`, `models/build.py` (or
equivalent), `schemas/`, `routers/orders.py`, `routers/products.py`, new Alembic migration.
Frontend: product settings (flag), build form, order detail, Dashboard "Build now".
Tests: new `backend/app/tests/test_personalised_allocation.py` and
`test_made_to_order_materials_reservation.py`, including the overselling case from section 1.

## 5. Onboarding & docs impact (per CLAUDE.md)

- **Seed/default:** none needed for a fresh install; the flag defaults off.
- **Setup order / checklist:** none. No new required step.
- **Empty states / intros:** the build form needs a short explanation when a personalised product
  asks for an order line; the product settings flag needs helper text.
- **Existing docs made wrong:** `docs/help/concepts.md` ("Made to order products are made after
  they're sold") and the "Orders and allocation" section must explain that personalised products
  aren't allocated automatically. Update `docs/help/builds-and-failures.md` for the
  order-line build and for failed builds. Add a `CHANGELOG.md` entry.
- **Locale:** nothing UK-specific.

## 6. Risks

- **Existing data.** Open orders for products that become personalised may already hold
  allocations. The migration must leave existing allocations alone and only apply the rule to new
  allocation attempts.
- **Shared materials.** The reservation in Phase 1 must not double-count when two products draw on
  the same material, or when substitutes/fallbacks are pooled.
- **Marketplace sync.** Etsy lines with quantity above 1 carry one personalisation for the whole
  line (decision 7.3). Orders imported before a product is flagged need a backfill decision.
- **Ordering of phases.** Phase 1 without Phase 2 leaves personalised orders with no way to
  allocate except existing manual paths. Ship them together, or have Phase 1 keep the current manual
  allocate route available for personalised lines in the interim.

## 7. Decisions needed

1. Is "personalised" the same as the existing `made_to_order` flag, or do some made-to-order
   products need to keep behaving as they do today?
2. Are personalised products ever built ahead of time (e.g. pre-engraved blanks)? If so, blanks
   become their own stocked product or material.
3. For lines with quantity 2 or more, is it always one personalisation across all units?
4. Start a tied build from the build form (pick the order line) or from the order (press Build)?
   The plan assumes both entry points share one backend rule.
5. Should Rule C (materials reservation) ship first, on its own, since it fixes an existing
   overselling bug?

## 8. Verification

- Unit tests for each rule above, including the 10 materials / 8 orders → push 2 case.
- Run the existing allocation, build, returns and listing-push test files.
- Manual check in the running app: place two orders for different text, build one tied to
  order 2, confirm order 1 stays unallocated and the pushed quantity drops correctly.
