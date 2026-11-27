# Design brief — Work flow

For Claude Design, in the **StockSmith UI** project. Companion to `docs/plan-workflow.md` (what and
why, including the decisions behind it) and `docs/help/workflow.md` (what a user is told). Where this
brief and the plan disagree, the plan wins.

## Status: built once, never designed

Work flow is **already built and working**, but it was built straight from the plan with no design
pass. Everything in `docs/design-review/workflow/` is the real running app, captured against a demo
shop, not a mockup. Treat the screens as a **functional first draft**: the behaviour is settled and
tested; the layout, hierarchy, wording and visual treatment are not. Redesign freely, but keep the
behaviour rules in "What must not change" below.

The code is the reference for what each control does:

| Screen | Code |
|---|---|
| Work page | `frontend/src/routes/work.tsx`, `frontend/src/components/work/WorkCards.tsx` |
| Settings → Work flow | `frontend/src/components/settings/WorkflowSettings.tsx` and `settings/workflow/` |
| Route field on a product | `frontend/src/components/products/WorkRouteField.tsx` |
| Dashboard tile | `frontend/src/components/work/WorkTodayTile.tsx` |

## What this is

StockSmith is a desktop stock-control app for small makers (candles, 3D printing, jewellery,
textiles). Between "an order arrived" and "it shipped" there is real work that differs by product:
cleaning, masking, sanding, making a personalised file, printing, gluing, packing. A busy maker
holds all of that in their head and re-decides what to do next every few minutes. **Work flow is a
to-do list that makes that decision for them.**

The person it was designed around: an Etsy seller with three printers, about 280 orders a month at
peak, four kinds of product, who works reactively and finds *choosing the next action* the tiring
part, more than the work itself.

It is **optional and off by default**. Switched off, the app looks exactly as it did before.

## The ideas a user has to grasp

Design so these are obvious without reading help:

1. **A product follows a route**: an ordered list of steps from first preparation to Ship, for example
   *Clean → Mask → Make → Assemble → Ship*. The user defines the steps and the routes.
2. **Make and Ship are fixed anchors.** Every route has exactly one of each, and Ship is last. Make is
   where a build is recorded; Ship is the existing order flow. Neither is ticked off on the Work page.
3. **The list shows what can be done *now*.** A step appears only when the one before it is done, so
   the list is short and actionable, never a wall of everything outstanding.
4. **Steps are grouped by station**, a place the user names ("Bench", "Printers", "Desk", "Packing").
   The point is to do everything at one place before moving to the next.
5. **Progress is a number, not a tick.** "Masked 4." A step can count in a different unit from finished
   items: a 6-pack of coasters needs six blanks cleaned, a 2-pack needs two.
6. **Preparation can run ahead of orders.** Work recorded with no order waiting is used by the next
   order. This is not shown as a separate thing; it simply makes later tasks disappear.
7. **An order ships whole.** It appears under *Ready to ship* only when every item in it is ready, and
   under *Waiting* (with the reason) until then.
8. **Designs mix only on purpose.** Printing several designs on one plate is efficient but they have to
   be sorted afterwards, so mixing is off by default and is a per-step choice.

## Constraints from the app

- **Light theme only, dense desktop UI** (12.5–13px). Read `.design-sync/conventions.md` and the
  project README: closed Tailwind vocabulary, use only classes that exist in `styles.css`.
- Colour meaning is fixed: slate = neutral, **amber = needs attention**, green = OK, red = late or
  destructive. A **late** task is red. *Waiting* is amber.
- Build from the real components: `SettingsCard`, `SegmentedControl`, `FilterTabs`, `Badge`, `Switch`,
  `FieldRow`, `EmptyState`, `ErrorBanner`, `SaveButton`, `Disclosure`, `Modal`, `ConfirmDialog`.
- **Not in the design system** (compose from primitives): the task card, the route builder, the station
  chips.
- **New sidebar item "Work"** in the **Sell** group, above Orders. It only exists while work flow is
  switched on. It has an icon in the same 16px `currentColor` style as the others.
- Dates and numbers follow the computer's locale. Never hard-code a format.
- The screens are used all day, often glanced at from arm's length, so the **next action must be
  legible at a glance**.

## Screens to review and design

Captured screenshots are named below. All are 1440 px wide at 2×.

### 1. Work page (`/work`): the priority

Screenshots: `02-work-page-all.png`, `03-work-page-bench-station.png`,
`04-work-page-packing-station.png`, `07-work-page-off.png`.

What it shows: station chips (with counts), then three sections:
**To do** (task cards), **Ready to ship**, **Waiting**. The first task is marked **Next**.

A **task card** carries: the step name and product, a large quantity (with its unit, e.g. "4 blanks"),
a per-design breakdown ("4 × Floral / 6 pack"), the orders it is for, a due badge (red when late), and
either a **Done** control (a number field and a button) or, for Make, a **Record build** link.

An **order card** carries: order number and buyer, "1 of 2 items ready", its lines (dimmed when not
ready), and for waiting orders why ("Waiting for: Phone stand: still to make").

Things to review, noticed while capturing and not yet fixed:

- **"Done" appears twice** in every card: as the field's label and as the button. Redundant.
- **What is the page's first glance?** Today the eye lands on a long list of equal-weight cards. Should
  the Next task be much larger than the rest? Should the page open on the user's current station?
- **The station chips** are the main navigation. Is a chip row right? They hold counts but no sense of
  urgency (no "2 late" cue).
- **Quantity versus unit.** "4 blanks" with "4 × Floral / 6 pack" beside it reads as two numbers
  competing. The first is what to do now (in the step's unit); the second is what it is for.
- **A mixed batch** (several designs, one plate) is the card where a mistake costs most. It needs a
  clear treatment. See `02-work-page-all.png`, the Make card under Printers.
- **Make cards** have no number field, only *Record build*, because making is recorded as a build with
  its own form. Does that read as inconsistent next to the other cards?
- **Ready to ship / Waiting** are shown below a possibly long To do list, so they can be out of sight.
  Is that the right priority? They are different kinds of thing (an order, not a step).
- **Undo** is a strip that appears at the bottom after recording something. It is easy to miss and
  disappears when the page reloads.
- **Empty and off states** are plain. See `07-work-page-off.png`.

### 2. Settings → Work flow

Screenshot: `01-settings-work-flow.png` (a tall page: switch, stages, routes, categories).

Four cards, top to bottom:

- **Work flow**: the on/off switch with a one-line explanation.
- **Stages**: a table of every step the user has defined (name, station, "per order", wait minutes),
  each editable in place and saved when the field loses focus, plus an "add stage" row. Make and Ship
  are built in: renameable, not deletable.
- **Routes**: one block per route. Each lists its steps in order with, per step, what it counts
  ("finished items" or "blanks in the recipe"), a **Mix designs** checkbox, move-up/down and remove.
  An *Add a step* picker and a *Save route* button close the block. A route is saved as a whole.
- **Product categories**: one row per category with a route picker.

Things to review:

- **The route block is dense and repeated.** Five routes means five near-identical blocks and a very
  long page. Should routes be a list with one open at a time (master-detail like Settings → Lists)?
- **"Mix designs" is shown on every step**, but it matters mainly at Make (and, for a user who preps
  mixed batches, preparation steps). Showing it everywhere invites changing it by accident.
- **"Counts"** is a jargon-y word for "what one unit of this step is measured in". Needs a better
  label, and perhaps an example inline.
- **Route-saving is explicit** (a Save route button per block) while stage edits save automatically.
  The two models side by side are confusing. Is there a consistent pattern?
- **Move up/down arrows** work but are slow for a long route. Drag to reorder is an option.
- **Ship is pinned last and Make is mandatory.** The UI hides the controls rather than explaining. Say
  why, once, plainly.
- **The relationship between the three lower cards** (stages → routes → categories) is the real mental
  model. Is the page making that order obvious, or just stacking three tables?

### 3. Route field on a product

Screenshot: `06-product-work-route-field.png`.

A "Work route" select in the product's Details tab, directly under *Product category*. The first
option inherits (*Follow the category (Ceramic coaster)* / *Follow the shop default (Standard)*).
It appears only while work flow is on.

Review: does it read as an *override* rather than a required choice? Is it in the right place, or
should it sit with the other stock-and-production settings?

### 4. Dashboard tile

Screenshot: `05-dashboard-with-work-tile.png`.

A single line above the dashboard's figures: "Work to do · 2 late · 1 due today · 1 ready to ship ·
Open Work". It is hidden when work flow is off or there is nothing to do.

Review: is a thin strip right, or should it be a proper card alongside the existing figures? It is the
only place most people will notice work flow exists.

### 5. Onboarding

Not built beyond a help page (`docs/help/workflow.md`) and the sample shop, which opens with work flow
on and a candle route. **Decide how a new user discovers it.** Today it is one switch in Settings. Does
it want an introduction, an optional step on the Dashboard checklist, or an empty-state invitation on
the Work page?

## What must not change

These are product decisions, made deliberately. Design around them:

- Every route has **exactly one Make** and **ends with Ship**. Make is recorded as a build, not ticked.
- The Work list shows what can be done **now**, not everything outstanding.
- **Mixing designs is off by default**, set per route step.
- **An order ships whole.** It is *Ready to ship* only when every item is ready.
- Progress is a **number**, entered against the card; fewer than shown records part of a task, more
  gets ahead.
- Per-order steps (for example "make the file" for a personalised item) are shown **one card per order**
  and are never merged.
- Nothing here changes stock, orders or builds. Switching it off just hides the page.

## Later phases: leave room, don't design yet

Documented in `docs/plan-workflow.md`:

- **Top-up cards** (phase 3): in quiet moments the list suggests making or preparing stock ahead
  ("Top up: 6 bottle openers. You sell about 4–5 a day, 2 on hand."). They are low priority, never
  ahead of an order, and the reasoning is shown. Likely a distinct card treatment.
- **Wait timers** (phase 4): a step that has to dry or cure is not actionable yet. Cards need a
  "ready at 14:00" state.
- **Time hints**: "you can fit three of these before the 2 pm cut-off."
- **Printer status** (later, separate plan): what is printing right now, per machine.

## Content and tone

Plain and brief. The user is mid-task, with sanded or glued hands. Prefer "Mask 4 blanks" to "Mask
stage: quantity 4". Say what to do, not what the system did. Avoid the word "demand".

## Done looks like

- The Work page's first glance is one obvious next action, with the rest clearly secondary.
- A mixed batch cannot be mistaken for an ordinary one.
- Route setup scales to a dozen routes without becoming a very long page.
- Every state is designed: off, nothing to do, one task, many, late, mixed batch, waiting, error.
- Everything uses existing components or is composed from them.

## How the screenshots were made

From the real app, run in a browser against a throwaway database holding an invented shop shaped like
the author's: 3D prints, personalised plaques, ceramic and wood coasters with 2-, 4- and 6-pack
variants, ten open orders, some part-prepared. Nothing in them is real customer data.
