# Plan — Pick list, pack list and box stickers

Written 2026-11-26 against 0.29.x, straight after storage locations (PR #316) landed. **Planning
only — nothing here is built.** Buyer names are deliberately out of the first build (§7).

Today there is nothing between "this order is ready to ship" and "it's in a box". This plan adds
three printouts that close that gap, all driven from a selection of ready-to-ship orders:

1. a **pick list** — what to take off the shelves, grouped by location, totals across orders;
2. a **pack list** — the same detail, one page per order, with multi-quantity lines made obvious
   and the box named;
3. a **box sticker** — order number and "box 1 of 2", printed on the existing label printer.

---

## 1. Decisions recorded

| Question | Decision | Consequence |
|---|---|---|
| Does the pick list include packaging (boxes, mailers)? | **Yes** (2026-11-26) | Kitting materials are summed across the selected orders and placed by location like products. |
| Items stored in several places? | **Show the primary place and its quantity; add a secondary place only when the primary can't cover the total** (2026-11-26) | A new placement-aware allocation step (§4.2), not just "primary location". |
| Do boxes get one sticker each? | **Yes — "box 1 of N"** (2026-11-26) | N comes from the confirmed boxes (§5). |
| Multi-box orders | **Must be confirmed before they can be picked, packed or stickered** (2026-11-26) | Builds on the existing box check (§5). |
| Sticker printer | **The existing label printer** (2026-11-26) | Reuses `BarcodeLabel` / Settings › Labels sizes. |
| Buyer name on printouts | **Ideally every storefront, pending terms. Not in the first build** (2026-11-26) | §7 holds the design and the gate. |

---

## 2. What already exists

Confirmed in the source on `main`, not assumed.

| Need | Existing thing | Path |
|---|---|---|
| Where an item lives, with quantities and a primary place | `locations`, `stock_placements` (`is_primary`), `placement_events` | `backend/app/models/location.py`, `docs/plan-locations.md` |
| "Pick from" per order line | Primary place plus "+N more places" on the order page | `frontend/src/routes/orders/$orderId.tsx` |
| Location order that matches the shelves | Count sheet sorted in shelf order | `docs/plan-locations.md` §7 |
| Which box an order uses | Box check: candidates, confirmation, signature | `backend/app/services/box_check.py` |
| Packaging an order will consume | `OrderKittingAllocation` (reserved/consumed per order and material) | `backend/app/models/kitting.py` |
| Personalisation text | `OrderLine.variation_text` | `backend/app/models/order.py` |
| "Ready to ship" state | `orderFulfilment()` and the Orders list tabs | `frontend/src/lib/orderFulfilment.ts`, `frontend/src/routes/orders/route.tsx` |
| Printing a small label | `BarcodeLabel`, `*-label.*` print routes, label sizes in Settings › Labels | `frontend/src/components/common/BarcodeLabel.tsx` |

**Not there:** any multi-select on the Orders list, any print view for orders, any shipping-label
printing (labels are bought and printed in Etsy's and eBay's own tools — see §6).

---

## 3. User flow

1. **Orders list:** tick the ready-to-ship orders you're processing (plus "select all ready").
2. A bar appears: **Print pick list · Print pack list · Print box stickers**.
3. Orders that can't go yet are shown unselectable with the reason (box check outstanding,
   cancellation pending review, unmapped items, nothing allocated).
4. Pick list prints first. Walk the shelves in order, tick off, take the pile to the packing desk.
5. Pack list: one page per order. Pack, stick the sticker on the box, label from the marketplace.

"Ready to ship" follows whatever `orderFulfilment()` calls the ship-ready state; confirm the exact
rule against it at build time rather than restating it here.

---

## 4. Pick list

### 4.1 Layout

Grouped by location in shelf order, then by item. Each row: item name and variant, SKU,
**total quantity**, and a tick box. Example:

```
Office › Shelf 01 › Grey Box 03
  [ ] Bottle opener – Walnut          10
Office › Shelf 02
  [ ] Postal box, small               10
No location recorded
  [ ] Gift tag                         4
```

- Products and packaging in one list, sorted by location only. A "Products / Packaging" label on
  each row, not separate sections, so one walk covers both.
- Footer: number of orders, number of lines, a printed date. A small order-number list per row
  ("#1042 ×2, #1051 ×8") is optional and off by default to keep the sheet short.
- Items with no placement fall into a **No location recorded** group at the end — never dropped.

### 4.2 Multi-place items (the one piece of real logic)

For each item the server takes the total needed across the selected orders and fills it from:

1. the **primary** place, up to the quantity placed there;
2. then other places, fullest first (the same rule stock deductions already use,
   `apply_stock_delta`), only for what the primary couldn't cover;
3. any remainder (stock exists but isn't placed) goes under **No location recorded**.

So one item can appear on two rows, e.g. "8 from Shelf 1, 2 from Loft". This is done **in the
backend** (one endpoint, unit-tested) rather than in the browser, so the screen can't disagree
with itself and the same result serves the pack list.

Placements sit beside the stock ledger, not in it (`plan-locations.md` §3), so this only reads —
it never moves stock.

### 4.3 Substitutions

The order page keeps substituted lines grouped together on purpose. The pick list works from the
**current** lines' items, so a substituted-in variant appears as itself. Pack list keeps the order's
own line order and labels the substitution. Both match what the order page shows.

---

## 5. Pack list and box stickers

### 5.1 Pack list (one page per order)

- Big order number; platform; ship-by date; shipping profile (e.g. the Royal Mail service).
- Each line: item, SKU, quantity, personalisation text if any.
- **Quantity above 1 is highlighted on that line** — large, bold, inverted "×3" — and must still
  read on a black-and-white laser printer, so no colour-only cues.
- **Box:** the confirmed box name(s) and quantity, in a boxed panel at the top.
- Pick location per line is optional (off by default) — the pick list already did that job.
- Never prints buyer note or any buyer detail in the first build (§7).

### 5.2 Boxes and the "must be resolved" rule

The box check already holds back an order whose items point at two or more different boxes until
a person confirms which were used. This plan makes it a gate for the new printouts too:

- An order with an unresolved box check is **not selectable**, with the reason shown and a link
  to its Box check panel.
- Number of stickers **N = the sum of confirmed box quantities** (one box type, quantity 2 →
  2 stickers; two different boxes → 2). An order with no box-category packaging at all gets 1.
- Change the confirmed boxes later and the existing signature mechanism already forces a fresh
  confirmation; the stickers simply follow it.

### 5.3 Sticker

- Same label printer and size presets as location labels. Contents: **order number large**,
  "Box 1 of 2", platform in small text, and a barcode of the order number so it can be scanned
  later (the location barcodes already set that precedent).
- Which number? Two exist: StockSmith's own order id (short) and the marketplace's reference
  (long). **Recommendation:** print the StockSmith id large and the marketplace reference small.
  The id is also what the pack list and order page lead with, so all three agree.

---

## 6. Why the sticker isn't tied to the shipping label

The request was to print the sticker when the shipping label is printed. StockSmith has **no
shipping-label feature** — labels are bought in Etsy's and eBay's own tools, and shipping
automation was parked on purpose (`roadmap.md`, `plan-marketplace-integrations.md` §2). So the
sticker prints with the pack list, from the same batch, a few seconds before the label comes out
of the other printer. Same result at the desk; no integration needed.

---

## 7. Buyer name (not in the first build)

**Status: deferred. Gate: read Etsy's *current* API terms first.** My search found only an
archived version, which bars storing member personal data without the member's affirmative
authorisation. That is not a confirmed reading of today's terms.

**Your idea — fetch, print, discard — is the right shape.** It matches how the sync preview already
treats receipts (shown, never saved). The design, if the terms allow it:

- A request made at print time, name held in memory for that one response, never written to the
  database, logs, query cache or a saved print file.
- Printed only; nothing re-prints it later (reprint = fetch again).
- Needs the store connected and online at print time.

**Where "all storefronts" runs into a wall:**

- **eBay.** The Account Deletion exemption is claimed on "never retrieves or stores member
  personal data". Fetching a name transiently still *retrieves* it, so the claim itself is likely
  broken, not just the storage half. The fallback is building the deletion-notification endpoint
  that `plan-marketplace-integrations.md` §2 avoided — a strategy decision, not a task. Default:
  **eBay excluded.**
- **Square.** Terms not checked at all.
- **Etsy.** Needs the terms read, and may need a scope the app doesn't request today.

It also touches the "no operated services, per-user developer apps" decision in
`plan-onboarding.md` §8, since each user's own app is the one making the request.

The first build leaves a blank slot in the pack-list header so adding a name later changes data,
not layout.

---

## 8. Build order

1. **Selection on the Orders list** — checkboxes, "select all ready", unselectable-with-reason.
2. **Pick-list endpoint + print view**, including packaging, the multi-place fill (§4.2) and the
   No-location group. Backend tests first; this is where the risk is.
3. **Pack list** — one page per order, highlighted multi-quantity lines, box panel.
4. **Box stickers** — N from confirmed boxes, label sizes, barcode.
5. **Help page and sample data** (§9).
6. **Buyer name** — only after the §7 gate, as its own change.

Each step is shippable alone; 1 + 2 already gives a working pick list.

---

## 9. Onboarding & docs impact

- **Seed / defaults:** none. Nothing here needs a default.
- **Setup order / checklist:** none. It's useful only once orders and locations exist; it adds no
  setup step. Without locations the pick list still works — everything lands in "No location
  recorded" — and should say "Set up locations to sort this by shelf" with a `HelpLink`.
- **Empty states and intros:** the Orders list needs a one-line explanation when nothing is
  ready to ship; the print screen needs a "nothing selected" state.
- **Help:** new `docs/help/picking-and-packing.md`, its slug in `HELP_SECTIONS` and a link from
  `docs/help/README.md` (the help test fails otherwise). Update `locations.md` (it currently
  describes only per-line "Pick from"), `concepts.md` and the box-check section of
  `material-categories.md`. Update the "location-sorted pick list" note in `plan-locations.md` §10.
- **Existing docs made wrong:** `plan-locations.md` §10's "not built" line; the sample workspace
  should include a few ready-to-ship orders across several locations so the feature can be tried.
- **Market-specific:** nothing UK-specific in the first build. Paper size must be a setting (A4
  default is UK-ish; Letter elsewhere) and dates and numbers follow the computer's locale. The
  Royal Mail service name is just the shipping profile's own name. The buyer-name step is the
  market-sensitive one (data protection differs by country).

---

## 10. Open questions

1. **Paper:** one pick list per page-set is assumed A4/Letter on a normal printer. Is that right,
   or do you pick from a handheld or phone?
2. **Print vs mark-as-picked:** should printing change anything (e.g. mark orders "picked" so
   they aren't printed twice), or stay a pure printout? **Recommendation:** pure printout first;
   a "picked" state is a new order status with its own consequences.
3. **Order-number-per-row on the pick list:** on or off by default (§4.1)?
4. **Etsy terms:** who reads them and when — this blocks §7 only, nothing else.
