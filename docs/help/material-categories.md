# Material categories

Every material belongs to a category. Categories group the Materials page, and each one carries a
few settings that change how StockSmith treats the materials in it.

You'll find them in **Settings › Lists › Material categories**. The set you start with depends on
what you told StockSmith you make when it first opened. Rename, add, reorder, merge or delete
them freely: a category can only be deleted once no material uses it, and merging moves every
material from one category into another.

## What each setting does

### Default unit

The unit a new material in this category starts with — grams for wax, metres for fabric, "each"
for boxes. You can still change it on any material. Leave it blank to keep whatever unit was
already picked.

### Has a colour

Shows a **Colour** field on materials in this category. Useful where the same material comes in
several colours: filament, beads, fabric, vessels.

### Has a material type (groups substitutable materials)

Shows a **Material type** field — PLA, soy wax, sterling silver, cotton. Materials in the same
category with the same type are treated as **interchangeable**: if one runs out, StockSmith can
offer another of the same type as a substitute when you record a build.

Turn this on where swapping suppliers or brands is normal. Leave it off where every material is
genuinely different.

### Show cost per kg

Shows average cost per kilogram instead of per gram. Only a display choice — for things bought
by the kilo but used by the gram, like filament or wax. Stock is still kept in grams.

### Consumed by failed builds

When you record a build with some failures, materials in this category are taken out of stock
for the failed items too. Turn it on for things that are used up even when a make goes wrong —
filament in a failed print, wax in a bad pour, fabric that's already been cut. Leave it off for
things you can reuse, like hardware or findings.

You can still change this per material when you record the build.

### Show in Kitting BOM list

Makes materials in this category available to choose in a [kitting BOM](concepts.md#kitting) —
the packaging that goes out with each product. Turn it on for packaging, labels and inserts.

### Kitting: one per order, not per unit

For packaging used once per order, however many items are in it: one mailer for three
candles, one thank-you card per parcel. Without it, an order of three would use three mailers.

Needs **Show in Kitting BOM list** turned on first.

### Kitting: check when an order has more than one

If an order's items would use two or more *different* materials from this category — say, two
different box sizes — the order is held until you confirm which packaging you actually used.
Use it for parcel boxes. Keep labels and tape in a category of their own, so they don't trigger
the check.

If the marketplace already shows the order as shipped (you bought the label there), the panel
says so and the button reads **Confirm boxes and ship**: confirming marks it shipped in
StockSmith too. Otherwise, ship it as usual once you've confirmed.

Needs **Kitting: one per order, not per unit** turned on first.

## Units

Units live next to categories, in **Settings › Lists › Units**. Each one has:

- **Decimal places** — how finely a quantity can be entered: 0 for things you count, 2 for
  metres, blank for no limit (grams, millilitres).
- **Show the name beside quantities** — "250 g" rather than "250". "Each" is usually left off,
  so a count of boxes just reads "12".

Renaming a unit updates every material that uses it.
