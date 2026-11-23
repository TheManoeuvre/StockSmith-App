# How StockSmith thinks

A handful of ideas sit under every page. Once these make sense, the rest of the app does too.

## Materials and products

**Materials** are what you buy: wax, filament, beads, fabric, boxes, labels. They're measured in
units — grams, millilitres, metres, or "each" for things you count.

**Products** are what you sell. You make products *from* materials.

Keeping the two apart is what lets StockSmith answer the useful questions: how many can I make
right now, what does one cost me, and what do I need to reorder.

## Material categories and types

Every material belongs to a **category** (wax, packaging, beads…). Categories group the
materials list, and a few category settings change how the app treats a material — for example,
whether it's used up when a make fails. See [Material categories](material-categories.md).

A **material type** is finer-grained: PLA, soy wax, sterling silver. Materials of the same
category and type can stand in for each other — if you run out of one supplier's soy wax,
StockSmith can suggest the other one.

## BOMs (recipes)

A product's **BOM** — bill of materials — is its recipe: each material it uses and how much,
for one product.

With a BOM, StockSmith can work out:

- **how many you could make** from the stock you have now;
- **what one costs to make**, from what you paid for each material.

A product without a BOM still sells, but StockSmith can't do either of those, and orders for it
are flagged **No BOM defined**.

## Variants

Many products come in options: sizes, colours, scents. Each option is a **variant**, with its
own SKU, price and stock. A variant uses the product's BOM unless you change it — on the
**Variants** tab you can override individual lines, so a large size uses more material than a
small one.

## Builds

When you make products, **record a build** on the product's **Stock** tab. A build:

- takes the BOM's materials out of stock;
- adds the finished products to stock.

If some didn't come out right, record them as failed. Materials in categories marked "consumed by
failed builds" are taken out for those too, because a failed pour or print still uses them up. You can scrap only part of a failed run — see
[Recording builds and failures](builds-and-failures.md).

**Made to order** products are made after they're sold rather than kept in stock.

## Orders and allocation

When an order arrives, StockSmith **allocates** (reserves) stock for it straight away, so the
same item can't be promised twice. If there isn't enough, the order shows on the Dashboard as
short of stock, with a **Build now** shortcut.

So a product has two numbers: what's on the shelf, and how much of that is already spoken for.
What's left is what you can still sell.

## Kitting

Packaging and extras go out with what you sell: a box, a mailer, tape, a card. Each product has a
**kitting BOM** listing them, and they come out of stock when an order for it is sent.

- The **default kitting BOM** (**Settings › Shipping & packaging**) is a starting point: it's
  copied into each *new* product's kitting BOM when the product is created. Changing it later
  doesn't change products that already exist.
- Change any product's own kitting BOM for items that need different packaging.
- Some packaging is used once per order however many items are in it — one mailer for three
  candles. Mark that category **Kitting: one per order, not per unit**. See
  [Material categories](material-categories.md).

Without kitting, packaging never comes out of stock and your margins leave out what it costs.

## Purchases

A **purchase** is an order you've placed with a supplier. Record it when you order, then mark
each line as received when it arrives. Receiving adds the material to stock and updates its
average cost from what you actually paid.

Give a supplier a **logo** to make its purchases easier to spot. In **Settings › Lists ›
Suppliers**, enter the supplier's website and StockSmith fetches the site's icon automatically.
If the icon is missing or poor, upload your own image instead — an uploaded logo always wins, and
removing it goes back to the website's icon. Purchases show the logo beside the supplier's name.

Materials you've ordered but not yet received count as **on order**, so StockSmith doesn't keep
telling you to reorder something that's already on its way.

## Reorder warnings and forecasts

From recent orders, StockSmith works out how fast each material is being used and how many
weeks your stock will last. The Materials page and Dashboard warn you when a material is running
low. The thresholds are in **Settings › Forecasting**.

## Stock takes

A **stock take** is a count of what's actually on your shelves. Approving one corrects
StockSmith's figures to match what you counted, and records the difference so you can see where
stock goes missing.

You don't have to count everything every time. Each item has a **count class** — A, B or C —
saying how often it's worth counting. A is counted most often, C least. Set the classes and how
often each is counted in **Settings › Stock counts**. The Dashboard shows what's due.

## Stores and listings

A connected **store** (Square, Etsy, eBay) brings its orders in. For Etsy and eBay, StockSmith
also keeps the **quantity** on your listings in step with your stock, so a sale on one platform
reduces what's shown as available on the other.

A **listing** is linked to a product (or variant) by **SKU**. Listings StockSmith can't match are
shown as unlinked, with tools to link them.

## Insights

**Insights** shows what's selling and what you actually earn after fees, postage, packaging,
materials and failed builds. It fills in as orders come in, and it's only as accurate as your
BOMs, kitting and fees.
