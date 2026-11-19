# Connecting Square

Connecting Square brings your Square orders into StockSmith alongside everything else, so stock
is reserved for them and they show on the Orders page. It's the simplest store to connect: you
paste one access token and pick a location. There's no developer app to register.

**What it does:** imports orders. **What it doesn't do:** StockSmith never changes anything in
Square — it doesn't push stock levels or listings there, and it doesn't store your customers'
names, emails or addresses (look those up in Square when you need them).

## Before you start

- A Square account with at least one **location** (Square creates one for you when you sign up).
- About 10 minutes.
- Your products set up in StockSmith with SKUs that match the SKUs on your Square items. Orders
  still import if they don't match, but each line will need linking by hand. See
  [Matching Square items to your products](#matching-square-items-to-your-products).

## 1. Get an access token from Square

Square gives access tokens through its Developer Console. You don't need to build anything —
you're only creating an "application" so Square has something to issue the token to.

1. Sign in to the **Square Developer Console** with your normal Square account.
2. Create a new application. The name is only for you — "StockSmith" is fine.
3. Open the application, switch to **Production** (not Sandbox), and open **Credentials**.
4. Copy the **Production access token**.

> Treat the access token like a password. Anyone who has it can read your Square account.
> StockSmith stores it encrypted on your computer and never displays it again.

**Sandbox** is Square's test environment, with fake data. Only use it if you're trying StockSmith
out with Square's test shop rather than your real one.

## 2. Connect it in StockSmith

1. Go to **Settings › Stores & sync** and choose **Square**.
2. Click **Connect**.
3. Paste the access token.
4. Under **Environment**, choose **Production** for your real shop (the dialog starts on Sandbox).
5. Click **Continue**. StockSmith checks the token with Square and lists your locations.
6. Choose the location your orders come from and click **Confirm**.

StockSmith syncs one location. If you sell from several, choose the one you want to track; you
can switch later from the Square store page without pasting the token again.

## 3. Choose where order history starts

On the Square store page, under **Order sync**:

1. Set **Import orders from** to the date you want StockSmith to start from. Orders placed before
   this date are never imported. For a new install, today (or the day you counted your stock) is
   usually right — importing months of old orders would reserve stock for things you've already
   sent.
2. Click **Save**.
3. Click **Preview** to see what would be imported without changing anything.
4. When the preview looks right, click **Sync now**.
5. Turn on **Automatic sync** to keep checking for new orders (every 15 minutes by default) while
   StockSmith is running.

## Matching Square items to your products

Each order line is matched to a StockSmith product or variant by its **SKU**. When a SKU matches,
stock is reserved straight away.

When it doesn't, the line shows **Unmapped SKU** on the order. From there you can link it to an
existing product, or create a new product from it. Fixing the SKU in Square (or in StockSmith)
means future orders for that item match on their own.

## Collection and delivery

Square orders show a **Collect** or **Delivery** tag next to the platform badge. Collect orders
use their collect-by date as the due date; others use the ship-by date.

## If something goes wrong

- **"Invalid token" or the connection fails** — check you copied the *Production* token and chose
  **Production** in StockSmith. A Sandbox token only works with Sandbox, and the other way round.
- **"This Square account has no locations"** — add a location in Square's own dashboard, then
  connect again.
- **Orders aren't appearing** — check the **Import orders from** date, and that you chose the
  location those orders were taken at.
- **Orders import with Unmapped SKU** — see [Matching Square items to your products](#matching-square-items-to-your-products).

Disconnecting Square, or restoring a backup onto another computer, means pasting a token again.
Store connections are deliberately not included in backups — see
[Backups](backup-and-restore.md).
