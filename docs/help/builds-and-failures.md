# Recording builds and failures

Record what you make on the product's **Stock** tab, under **Record a build**. If the product has
variants, pick the one you made first.

## A successful build

Enter **Qty built** and press **Record**. StockSmith:

- takes the recipe's materials out of stock (quantity per product × number built);
- adds the finished products to stock.

## Failed units

If some didn't come out right, enter **Qty failed** as well. That's the number of *products* that
failed. Failed products never go into stock, but they can still use up materials.

When Qty failed is above 0, a panel appears asking **which materials were consumed** for the
failed units. For each material in the recipe you can set:

- **Consumed** (the checkbox) — whether this material was actually used up. Materials in
  categories marked "consumed by failed builds" are ticked by default (see
  [Material categories](material-categories.md#consumed-by-failed-builds)). Untick anything the
  failed run never reached.
- **Units scrapped** — how many products' worth of that material to scrap. It starts at Qty
  failed, which is the right answer when every failed product was a complete write-off. Lower it
  when only part of the run was lost.

Next to each material StockSmith shows the sum it will apply, so you can check it before you
press Record: **units scrapped × quantity per product = amount scrapped**.

### Worked examples

**A coaster set uses 2 blanks per set, and 2 sets fail completely.** Set Qty failed to 2 and leave
Units scrapped at 2. The panel reads `2 × 2 blanks per unit = 4 blanks scrapped`, and 4 blanks
come out of stock.

**One coaster of a 2-coaster set is ruined.** Set Qty failed to 1, then set the blanks'
Units scrapped to 0.5. That scraps `0.5 × 2 = 1` blank, not 2.

**A 3D print fails half way.** Set Qty failed to 1 and Units scrapped to 0.5 on the filament
(a rough estimate is fine). Half the filament for one print is taken out of stock.

Units scrapped can be a fraction, and it can differ per material: for instance, the filament may
be fully used but the metal insert never reached.

## When a material runs short

If a build would use more of a material than you have, StockSmith says so and offers any
fallback materials you've set up for it. Failed units count towards the amount needed.

## Stock history

A failed run shows in the product's stock history as a **Failed build**. It doesn't change the
product's stock count, because nothing usable was made. The materials it used up appear in each
material's own history as "(failed units)".

## Fixing a mistake

If you recorded the wrong numbers, correct material stock with an adjustment on the material's
**Stock** tab, or product stock with **Adjust built stock** on the product's.
