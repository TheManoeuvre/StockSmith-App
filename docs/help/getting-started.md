# Getting started

StockSmith works best set up in a particular order. Each step uses what the one before it
created, so working through them in sequence saves going back.

You don't need to finish in one sitting. Your progress is saved as you go.

## 1. Tell StockSmith what you make

The first time it opens, StockSmith asks what kind of business you run (3D printing, candles &
soap, jewellery, textiles, paper goods, or something else). Your answer sets up
[material categories](material-categories.md), units and a few example material types to match.
Nothing here is fixed — rename, add or delete any of it in **Settings › Lists**.

Moving from another computer? Choose **Restore a backup instead** and follow
[Moving to a new computer](backup-and-restore.md#moving-to-a-new-computer).

### Want to look around first?

Choose **Look around with sample data first** on that screen, or from **Help** at any time.
StockSmith restarts into a made-up candle shop with three months of products, stock, orders and
sales, so you can try every page without touching your own data. Change anything you like.

A yellow bar across the top shows you're in the sample. **Leave sample data** restarts StockSmith
with your own data and deletes the sample. While you're in the sample, your own stores don't sync.

## 2. Add your materials

Materials are everything you buy and use up: raw materials, components and packaging.

1. Open **Materials** and add each one, choosing its category and unit.
2. Add suppliers as you go, so reordering and purchase records know where things come from.

Got a lot? The Materials page has **Import CSV**. Click **Export CSV** first to get a file with
the right columns, fill it in in a spreadsheet, and import it back.

## 3. Count what you have

StockSmith needs to know how much of each material you have right now.

- For a handful of materials: open each one and use **Stock adjustment › Stock count (set exact
  amount)**.
- For lots: start a **Stock Take**, count everything on the sheet, then **Review and approve**. This is also a
  good habit to keep — see [Stock takes](concepts.md#stock-takes).

**You're at your first milestone:** StockSmith now knows what you've got.

## 4. Set up your usual packaging — before adding products

The packaging that goes out with your products — a box, a mailer, tape, a thank-you card — is
the **kitting BOM**. Set your usual packaging once as the **default kitting BOM** under
**Settings › Shipping & packaging**, and every product you add *afterwards* starts with it.

Do this before step 5: products that already exist don't pick up later changes to the default.
See [Kitting](concepts.md#kitting).

## 5. Add your products and their recipes

A product is something you sell. Its **BOM** (bill of materials) is the recipe: which materials
go into one, and how much of each.

1. Open **Products** and add a product.
2. On its **BOM** tab, add each material and the amount one product uses.
3. If it comes in options (sizes, colours, scents), add them on the **Variants** tab. Each
   variant can change the recipe — a large candle uses more wax.
4. If this product ships in different packaging from your usual, change its kitting BOM.

### Importing a whole catalogue

**Products › Import / export** takes three spreadsheet (CSV) files, imported in this order:

1. **Products** — name, SKU and description.
2. **Variants** — one row per variant, with its product's SKU, its name, its attributes and price.
3. **Recipes (BOMs)** — one row per material in a recipe: the product's SKU, the material's name
   and how much one uses. Leave **variant_name** blank for the product's own recipe; fill it in
   for a variant that uses a different amount or an extra material. To swap one material for
   another in a variant — white instead of black, say — put the material it replaces in
   **replaces_material**.

Export each file first to get the right columns. Importing a recipe replaces that recipe as a
whole, and a recipe with any mistake in it is left unchanged, with the mistake listed so you can
fix it and import again.

Once a product has a BOM, StockSmith can tell you how many you could make from the stock you
have, and what each one costs to make.

**You're at your second milestone:** StockSmith knows what you can make and what it costs.

### Optional: say where things are kept

Once your stock is in, you can record **where** it lives, from a room down to a box, on the
**Locations** page. It's optional and can wait. See [Locations](locations.md).

## 6. Bring in your orders

Either connect a store so orders arrive by themselves, or add orders by hand on the **Orders**
page.

- [Connecting Square](connecting-square.md) — about 10 minutes.
- [Connecting Etsy](connecting-etsy.md) and [Connecting eBay](connecting-ebay.md) — these need
  your own developer account on each platform, so allow an afternoon, and longer if the platform
  has to approve your account.

**Orders are imported from the day you connect.** Older orders would arrive with no costs
recorded and look like work still to do. If you have orders waiting to be sent, move the store's
**Import orders from** date back to cover them before your first sync.

## 7. Check your fees and postage

For your margins to be right:

- **Settings › Pricing & fees** has Etsy and eBay's standard UK fees filled in. Selling from
  elsewhere? Replace them with your country's. Either way, check they match your account — fees
  change, and some (like offsite ads) only apply to some sellers.
- **Settings › Shipping & packaging** starts with three Royal Mail 2nd Class options (large letter,
  small parcel, medium parcel) at Post Office counter prices, charged to the buyer at cost. Change
  them to what you actually pay — buying postage online is usually cheaper — and to what you charge.
  Not in the UK? Archive them and add your own carrier's.

## 8. Keep a backup somewhere else

Backups run every day by default, but they're saved on the same computer. Point **Settings ›
Backup & restore › Also copy each backup to** at a OneDrive or Dropbox folder, so a broken
computer doesn't take your records with it. See [Backups](backup-and-restore.md).

## What next

- [How StockSmith thinks](concepts.md) explains the ideas behind each page.
- Juggling several kinds of product with different steps before they ship? [Work flow](workflow.md) is
  an optional to-do list that tells you what to do next.
- The **Dashboard** shows what needs doing today: orders short of stock, materials running low,
  and items due for counting.
