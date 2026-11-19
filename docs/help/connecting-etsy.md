# Connecting Etsy

Connecting Etsy imports your Etsy orders and keeps the quantity on your Etsy listings in step
with your stock.

> **This is an advanced setup.** Etsy only lets apps connect to a shop through a registered
> developer app, and StockSmith doesn't provide a shared one — each shop registers its own. That
> keeps your Etsy access entirely between you and Etsy, but it means a one-off visit to Etsy's
> developer site. Allow an afternoon, plus however long Etsy takes to approve your app.
>
> Etsy changes its developer site from time to time. If a step below doesn't match what you see,
> look for the same values under slightly different names.

**What StockSmith changes on Etsy:** listing quantities only (and, if you use them, the listing
tools described at the end). It never changes prices, titles or anything else without you
starting it.

## 1. Register a developer app with Etsy

1. Sign in to Etsy's developer site with the Etsy account that owns your shop.
2. Create a new app. Describe it honestly — for example, "Private inventory tool for my own shop".
3. Once it's created, note two values:
   - the **keystring** (StockSmith calls this the **Client ID**);
   - the **shared secret** (StockSmith calls this the **Client secret**).
4. Add this as the app's **callback URL**, exactly:

   ```
   http://127.0.0.1:8000/api/v1/platforms/etsy/callback
   ```

   This address only exists on your own computer: it's where your browser hands Etsy's approval
   back to StockSmith.

Etsy may need to approve the app before it can connect. If Etsy refuses the callback URL because
it isn't `https://`, you'll need a small redirect service like the one described for eBay — see
[Connecting eBay › Set up a redirect](connecting-ebay.md#3-set-up-a-redirect) and use the Etsy
callback address above in place of eBay's.

## 2. Enter the app's details in StockSmith

1. Go to **Settings › Stores & sync** and choose **Etsy**.
2. Open the **Developer app** card and its credentials.
3. Enter the **Client ID** and **Client secret** from step 1.
4. Set **Public base URL** to `http://127.0.0.1:8000` (or the address of your redirect service,
   if you needed one). StockSmith builds the callback URL from it, and it must match what you
   registered with Etsy.
5. Click **Save credentials**.

## 3. Connect your shop

1. At the top of the Etsy store page, click **Connect**.
2. Your browser opens Etsy. Sign in if asked, and allow access.
3. The browser shows a "connected" page. Return to StockSmith.

## 4. Choose where order history starts

Under **Order sync**:

1. Set **Import orders from**. Orders placed before this date are never imported — leave it empty
   and StockSmith fetches your entire history. For a new install, choose today or the day you
   counted your stock.
2. Click **Save**, then **Preview** to check what would be imported.
3. Click **Sync now**, then turn on **Automatic sync**.

## 5. Link your existing listings

Order lines and listings are matched to your products by **SKU**. If your Etsy listings already
use the same SKUs as your StockSmith products, they link up on their own.

The **Tools** card on the Etsy store page helps with the rest:

- **Compatibility report** — shows which products and listings match, and which don't.
- **Unlinked listings** — listings StockSmith can't match yet, with a way to link each to a
  product. Linking writes StockSmith's SKU onto the Etsy listing; it's an ordinary listing edit
  you can change back.
- **Backfill from Etsy** — copies descriptions, prices and photos from your Etsy listings into
  your StockSmith products, so you don't have to type them in again.

## If something goes wrong

- **"public_base_url is not configured"** — complete step 2.4 and save.
- **Etsy says the redirect URI doesn't match** — the callback URL registered with Etsy and the one
  StockSmith builds from **Public base URL** must be identical, including `http` vs `https` and the
  port.
- **"Missing or expired authorization state"** — the approval took too long or StockSmith was
  restarted in between. Click **Connect** again.
- **Orders import as Unmapped SKU** — the SKU on the Etsy listing doesn't match any product. Fix it
  from the order, or link the listing as in step 5.

Store connections aren't included in backups. After restoring onto another computer, connect
again — your developer app and its details stay the same.
