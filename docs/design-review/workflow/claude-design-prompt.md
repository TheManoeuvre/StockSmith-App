# Prompt for Claude Design: Work flow

Paste everything below the line into a new design in the **StockSmith UI** project. Attach
`docs/design-brief-workflow.md` and the seven screenshots in this folder first.

---

I need you to review and redesign a feature of StockSmith that was built without a design pass. It
works and its behaviour is settled, but its layout, hierarchy and wording have never been designed.

**Attached:** `design-brief-workflow.md` (read all of it first; it is the source of truth) and seven
screenshots of the running app, numbered 01–07. The screenshots are real, captured from the live app
against an invented demo shop, so you are critiquing what exists, not a mockup.

## The feature in one paragraph

StockSmith is a dense desktop stock-control app for small makers. **Work flow** is an optional to-do
list that tells a busy maker what to do next. Each product follows a *route* (an ordered list of steps
they define, such as Clean → Mask → Make → Assemble → Ship). A *Work page* shows only what can be done
right now, grouped by station (Bench, Printers, Desk, Packing), most urgent first, and orders ship
whole once every item is ready. The person it was designed for is an Etsy seller with three 3D
printers who finds choosing the next action more tiring than doing the work.

## What I want, in this order

1. **A short critique first.** Before drawing anything, list what is wrong or weak in each screenshot,
   most important first. The brief already names several; add what you see. Be direct.
2. **Redesign the Work page** (screenshots 02, 03, 04, 07). This is the priority. Its first glance
   should be one obvious next action with everything else clearly secondary. Design these states:
   - a normal day with a mix of tasks across stations
   - a single task
   - nothing to do
   - work flow switched off
   - a **late** task
   - a **mixed batch** (several designs printed on one plate); this is the card where a mistake costs
     the most, so it must not be mistakable for an ordinary one
   - a **Make** task, which has no number field and instead links to a build form
   - an order **ready to ship** and an order **waiting** (with the reason)
   - an error when recording progress
3. **Redesign Settings → Work flow** (screenshot 01). The route blocks repeat and make the page very
   long; propose how it should scale to a dozen routes (the Settings → Lists page is the master-detail
   pattern in this app). Fix the confusing mix of automatic and explicit saving, the jargon label
   "Counts", and the way "Mix designs" is offered on every step. Make the order of stages → routes →
   categories obvious.
4. **The smaller pieces:** the route field on a product (06), the dashboard tile (05), and how a new
   user discovers the feature (see "Onboarding" in the brief).
5. **Leave room, don't design,** the later phases listed in the brief (top-up suggestions, wait timers,
   time hints, printer status).

## Rules you must follow

- Build from the project's real components and Tailwind vocabulary only. Compose anything missing
  (task card, route builder, station chips) from primitives. Read the project README and
  `.design-sync/conventions.md`.
- Light theme, dense (about 12.5–13px). Colour meaning is fixed: slate neutral, **amber needs
  attention** (waiting), green OK, **red late or destructive**.
- Dates and numbers follow the computer's locale; never hard-code a format.
- Keep to the brief's **"What must not change"**: every route has exactly one Make and ends with
  Ship; Make is recorded as a build and not ticked; the list shows what can be done *now*; mixing
  designs is off by default; an order ships whole; progress is a number you enter.
- Plain, brief wording. The user is mid-task with sanded or glued hands. Prefer "Mask 4 blanks" to
  "Mask stage: quantity 4". Avoid the word "demand".

## What I need back

- The critique (step 1).
- Designs for every state above, desktop width.
- For each decision that departs from the screenshots, one sentence on why.
- A list of anything you would need to change in the app's behaviour (not just its look) to make the
  design work, kept separate so I can judge it.
- Anything in the brief that you think is wrong or missing.

If something in the brief is ambiguous, ask me before inventing an answer.
