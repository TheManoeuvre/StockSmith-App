# Work flow

Work flow turns your orders into a to-do list: what to prepare, make, finish and pack next, grouped
by where you do it, most urgent first. It is for the moments when you're juggling several kinds of
product and can't tell what to pick up next.

It is optional and **off until you turn it on**. It only reads your orders, builds and stock, so it
never changes how any of those behave. Switching it off just hides the Work page.

## What it does

Every product follows a **route**: the steps between "an order arrived" and "it shipped". The
simplest route is **Make → Ship**, which is what everything follows until you say otherwise. A
route for a ceramic coaster might be **Pre-clean → Mask → Make → Assemble → Ship**.

The **Work** page, under **Sell** in the sidebar, shows what is outstanding at each step. When you
finish something you tell it how many you did, and the list moves on.

Work flow does not touch your stock. Making still means recording a [build](builds-and-failures.md),
and shipping still happens on the order.

## Turn it on

Open **Settings → Work flow** and tick **Use work flow**. A **Work** entry appears in the sidebar.

## Stages and stations

A **stage** is one step, like *Sand*, *Mask* or *Assemble*. You make your own under **Settings →
Work flow → Stages**.

Two are built in and can be renamed but not removed:

- **Make** is the step that produces the item. Completing it is recording a build.
- **Ship** is the last step, and is the order itself.

A stage can have a **station**: where you do it, such as *Desk*, *Bench*, *Printers* or *Pack*. The
Work page groups tasks by station so you can do everything at one place before moving to the next.
Stations are just words you choose.

A stage can also be **per order**, for work that only makes sense for one buyer, like making a
personalised file. Per-order work is shown separately for each order, is never combined with
another, and an item can't be made until it's done.

## Routes

A route is the order the stages happen in. Build them under **Settings → Work flow → Routes**.

Every route needs exactly one **Make** and ends with **Ship**. Steps before Make are preparation;
steps between Make and Ship are finishing, such as gluing. Use the arrows to reorder and **Save
route** to keep your changes.

### Which products follow which route

- Under **Product categories**, choose a route for each category. Every product in it follows that
  route.
- On a product's own page you can choose a different route for just that product.
- Anything with no choice follows the **shop default** route.

## Pack sizes: counting by material

A 6-pack of coasters needs six coasters cleaned and masked; a 2-pack needs two. To get this right,
set a step to count **by a material category** in the route (for example *Blanks in the recipe*)
instead of *finished items*.

The step then asks for as many as the product's recipe holds: six for a 6-pack, two for a 2-pack.
The number you tick off is in the same units, so "masked 4" means four coasters.

## Preparing ahead

You can record preparation before any order needs it. If you clean and mask six coasters in a quiet
spell, the next order for them skips those steps and goes straight to Make. This also means prep
isn't wasted if an order is cancelled.

Preparation isn't tied to an order, except for per-order stages.

## Mixing designs on one plate

Printing several designs on one plate is efficient, but the items have to be sorted afterwards. So
mixing is **off by default**. Tick **Mix designs** on a route step to let one batch combine
designs (variants) of a product that would print from the same materials.

- A mixed batch is shown as one task, with the quantity of each design listed.
- Designs that use different materials are never combined. Black PETG and black PLA stay apart.
- Each order's own **Ship** card lists exactly which designs go in its box.

## The Work page

Tasks are grouped by station. Choose a station to see only what you can do where you are. The most
urgent task, the earliest due, is first and is shown as **Next**.

- **Done** records how many you did. Enter fewer than shown to record part of a task, or more to get
  ahead. **Undo** takes back the last thing you recorded.
- **Make** tasks open the build form instead, since making is recorded as a build.
- **Ready to ship** lists orders you can pack now. An order ships whole, so it appears here only when
  every item in it is ready.
- **Waiting** lists orders that aren't ready yet and what each is waiting for.

Due dates come from each order's ship-by date (or collect-by date). An order with no date is listed
last.

## Things to know

- Work is counted from the day you first turned work flow on. Stock you already had, and builds from
  before then, are treated as finished.
- Stock that is already on the shelf when an order arrives skips the route and goes straight to
  Ship.
- If you build something on the ordinary build form rather than from the Work page, it still
  counts.
- Preparation is counted per variant. Cleaning six blanks for a 2-pack doesn't count toward a
  6-pack.
