# Design brief — Storage locations

For Claude Design, in the **StockSmith UI** project. Companion to `docs/plan-locations.md`, which
holds the data model and behaviour; this brief covers only what needs designing. Where the two
disagree, the plan wins.

## What this is

StockSmith is a desktop stock-control app for small makers (candles, 3D printing, jewellery,
textiles). Today it knows **how much** of each material and product there is, not **where**. This
feature adds **locations**: a free-form tree the user builds themselves, which materials and
finished products can be stored in.

Examples of what a user might build, at very different depths:

- `Office › Shelf 01 › Grey Box 03`
- `Box A1` (one level, nothing else)
- `Workshop › Oils rack › Bin 1`, `Bin 2`, `Bin 3`

There are **no fixed levels and no level names**. Depth is the user's choice.

It's **optional**. Most users will set it up some time after their stock is in.

## The ideas a user has to grasp

Design so these are obvious without reading help:

1. **Locations nest.** A location can contain other locations and/or stock.
2. **Stock is placed with a quantity.** "5 of these spools are on Shelf 1, 20 are in the Loft." Stock
   not yet placed anywhere is **Unassigned** (item total minus everything placed). Unassigned is
   normal, not an error.
3. **One location per item can be the "primary" one**, the place you pick from. Overflow lives
   elsewhere.
4. **Single-location items stay correct on their own.** If an item is in exactly one location, builds
   and sales deduct from it and deliveries add to it automatically. Items in several locations don't.
   The UI should make the difference *visible*, so users aren't surprised when one does and one
   doesn't.
5. **Placed quantities can drift** from the real total. Show it, offer a one-click fix, never hide it.

## Constraints from the app

- **Light theme only, dense desktop UI** (12.5–13px). Read `.design-sync/conventions.md` / the
  project README: closed Tailwind vocabulary, use only classes that exist in `styles.css`.
- Colour meaning is fixed: slate = neutral, **amber = warning/needs attention**, green = OK,
  red = destructive. **Drift and "unplaced" are amber, not red.**
- Build from the real components: `FilterTabs`, `Tabs`, `Th`/`GroupHeaderRow`, `Badge`, `Modal`,
  `ConfirmDialog`, `DetailPanel`, `FieldRow`, `SegmentedControl`, `Switch`, `Stat`, `SaveButton`,
  `Disclosure`, `ErrorBanner`, `BarcodeLabel`.
- **Not in the design system** (compose them yourself from primitives): the tree, the master-detail
  layout, the range preview list, the label layouts. Use the Settings **Lists** page (list/tree on
  the left, detail on the right) as the pattern to follow for master-detail.
- **New nav item "Locations"** in the **Stock** group, between **Purchases** and **Stock Take**
  (sidebar order: Materials, Purchases, **Locations**, Stock Take). It needs an icon in the same
  16px `currentColor` style as the others.
- Dates and numbers follow the computer's locale. Never hard-code a format.

## Screens to design

Phase 1 is the priority; phases 2–4 are noted so the layouts leave room.

### 1. Locations page (`/locations`) — phase 1

**Left: the tree.** Expand/collapse, an item count per location, a search box, an *Add location*
button, and a filter control for: *All · Needs attention · Unplaced stock · Empty locations*. Show
archived locations only behind a toggle.

**Right: the selected location.**
- Breadcrumb path (`Office › Shelf 01 › Grey Box 03`), each segment clickable.
- Its short scan code (`LOC-000042`) with a copy button.
- **Contents** table: item, SKU, qty, unit, and a *primary* marker. Locations that contain
  other locations also list their children with a rolled-up item count.
- Actions: **Add child · Generate range · Move location · Empty into… · Print label · Archive**.
  *Count this location* appears here in phase 3 — leave room.
- A collapsed **History** section (moves, auto-deducts, auto-adds, with dates).

**States to show:** empty first run; a large tree; a location with items; an empty location; one with
drift; archived.

**Empty first run** (no locations exist): a heading like *Where do you keep things?*, one sentence
on nesting, a primary **Add a location** button, and a **Generate a range** option with the example
`Box{01-05}` shown inline. This is also the page intro; it can be dismissed once locations exist.

### 2. Generate range dialog — phase 1

A `Modal`. Fields: **parent location** (picker, blank = top level), **pattern**, and a live
**preview**. This dialog is the delight moment of the feature, so spend design effort here.

- Pattern examples shown as clickable chips that fill the field:

  | Pattern | Result |
  |---|---|
  | `Box{01-05}` | Box01 … Box05 (padding follows the start value) |
  | `Bin-{A-F}` | Bin-A … Bin-F |
  | `Shelf {1-3}` | Shelf 1 … Shelf 3 |
  | `{A-D}{1-6}` | A1 … D6, a grid of 24 |
  | `Col{AA-AD}` | Col AA … Col AD |

- The preview shows the first few and last few names with a total ("24 locations"), flags any that
  **already exist under that parent (skipped)**, and shows clear errors: end before start, too many
  tokens, **over the 500 limit**.
- Footer: **Create 24 locations** (disabled until valid), and after creating, a prominent
  **Print labels for these** follow-up.

### 3. Move dialogs — phase 1

Three related actions. Make it unmistakable which one the user is doing:

- **Move location** ("move Box01 from Shelf 1 to Shelf 2"): pick a new parent. State plainly that
  its contents move with it. Prevent choosing itself or a descendant, and say why.
- **Empty into…** ("move everything in Box01 into Box02"): pick a destination. Shows how many
  items and what will merge. The source location stays, empty.
- **Move item** ("move 4 of this from the Loft to Shelf 1"): item, from, to, quantity, and what
  will remain in each.

Moves never change an item's total. Say so in the confirmation copy.

### 4. "Stored in" section on items — phase 1

Appears on the **material detail** and the **product / variant stock section**. This is where most
people will meet the feature, so it must be compact.

**States:**
- **Not placed anywhere:** "Not stored anywhere yet" + **Place** action.
- **One location:** location path, qty, a small *"Updates automatically"* hint (with a tooltip
  explaining why this item follows stock by itself).
- **Several locations:** a small table, the primary marked, an **Unassigned** row at the bottom, and a
  quiet note that stock changes for this item are not deducted automatically.
- **Unassigned remainder** after a delivery: a *Put away* prompt.
- **Drift:** amber banner, *"Placed 5, but only 3 in stock"*, with **Reduce Shelf 1 by 2**.

**Also:** a **Location** column on the Materials and Products lists (primary location, `+2 more` if
split) and filters *Unplaced* / *In location…*.

### 5. Label printing — phase 2 (design the layouts now)

Labels print from the browser print dialog onto a thermal label printer. Content: the location's
**path**, its **name**, and a **Code 128 barcode of the scan code** (`LOC-000042`, fixed length, so
the barcode width is predictable).

**Two layouts are required, and they differ in hierarchy, not just scale:**

| Layout | Size | Direction |
|---|---|---|
| **Small (default)** | **51 × 25 mm**, landscape | Very little room. The location **name is the headline**; the parent path is secondary, small, one or two lines, truncated with an ellipsis rather than shrunk below legible. Barcode fills the remaining height. Show the human-readable code only if it fits. |
| **Large** | **6 × 4 in (152.4 × 101.6 mm)**, landscape | Roomy. Full path in large text (it can wrap), a large barcode, and the code in readable type underneath. Works as a shelf or bay sign as well as a box label. |
| Custom | any width × height in mm | Uses the closer of the two layouts. |

Show both as to-scale print previews, including a **worst case**: a deep path
(`Warehouse Annexe › Rack 04 › Shelf C › Grey Box 03`) on the small label.

**Print screen:** pick a size (51 × 25 / 6 × 4 / custom), tick one or many locations from the tree
(and *this location and everything beneath it*), preview, **Print**. Reached from the Locations
page, from the range dialog's follow-up, and from the location detail.

Note: the app's existing material/product label is a hard-coded 50 × 25 mm with the item name above
a barcode. That's separate and unchanged, but the new label should look related to it.

**Settings:** a small "Labels" setting for the default size.

### 6. Onboarding — phase 1

- A new **optional** step in the Dashboard **setup checklist**: *"Set up your storage locations"*,
  placed after opening stock is counted, deep-linking to `/locations`. Match the existing steps'
  title / hint / link pattern.
- The **Locations page intro** (see screen 1) with a "Learn more" link to the help page.
- On a populated Locations page where stock exists but nothing is placed: an empty-ish state,
  *"Place your stock"*.
- The sample workspace (a candle maker) will ship with locations, so design with believable data:
  `Workshop › Wax shelf`, `Workshop › Oils rack › Bin 1–3`, `Workshop › Jar store`,
  `Stockroom › Shelf A`, `Loft`.

### Later phases — leave room, don't design yet

- **Count by location** (phase 3): a stock-take sheet grouped by location, sorted in shelf order,
  with *scan a location to open its sheet* and *add an item found here*.
- **Put-away and pick locations** (phase 4): a destination chooser when receiving a delivery, a
  pick location shown beside each order line, and an inline "Taking from: [Shelf 1 ▾]" field when
  recording a build for an item stored in several places.
- **Global scan / search box:** accepts a typed or scanned `LOC-…` code and jumps to that location.
  Worth reserving a place for it in the Locations page header.

## Content and tone

- Plain, practical words: *location, place, move, put away, unassigned*. Avoid *bin* / *warehouse* /
  *SKU-location* jargon in UI copy (those are only example names).
- Never alarm: unplaced stock and drift are routine, so use amber and a clear next action.
- Always show a way out of a bulk action: a preview before create, a count in the confirm button.

## Done looks like

- Screens 1–4 and 6 designed for the states listed, in the app's existing look.
- Both label layouts shown to scale, with the worst-case path.
- Anything that needs a component not in the design system called out, so we know what to build.
