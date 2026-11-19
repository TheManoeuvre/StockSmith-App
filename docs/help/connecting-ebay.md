# Connecting eBay

Connecting eBay imports your eBay orders, keeps the quantity on your eBay listings in step with
your stock, and fetches the fees eBay charged on each order.

> **This is the most involved setup in StockSmith.** eBay requires every app to have its own
> developer account, and StockSmith doesn't provide a shared one — so you register your own. You'll
> need: an eBay developer account (eBay may take a day or so to approve it), a production
> keyset, and a small free redirect service, because eBay only sends approvals to `https://`
> addresses. Allow an afternoon once your developer account is approved.
>
> eBay changes its developer site from time to time. If a step below doesn't match what you see,
> look for the same values under slightly different names.

## 1. Get an eBay developer account

1. Go to eBay's developer program site and join, using the eBay account that owns your shop.
2. Wait for eBay to confirm the account.

## 2. Create a production keyset

1. In the developer site, create an application keyset for **Production** (Sandbox is eBay's test
   environment with fake data).
2. Note two values:
   - **App ID** — StockSmith calls this the **Client ID**;
   - **Cert ID** — StockSmith calls this the **Client secret**.

### Marketplace account deletion

Before a production keyset works, eBay asks every app either to receive notifications when an
eBay user deletes their account, or to declare it's exempt because it doesn't keep eBay users'
personal data.

StockSmith is built not to store buyers' names, addresses or contact details — order lines,
SKUs, amounts and eBay's order number only. Whether you claim the exemption is your declaration
to eBay, about how you use the app.

## 3. Set up a redirect

When you approve StockSmith, eBay sends your browser to an **accepted URL** — and it only accepts
`https://` addresses. StockSmith runs on your own computer at `http://127.0.0.1:8000`, so you
need a tiny service on the internet whose only job is to pass that approval straight back to your
computer. It never sees your password and stores nothing.

One free way is a **Cloudflare Worker**:

1. Create a free Cloudflare account and create a new Worker.
2. Replace its code with:

   ```js
   export default {
     async fetch(request) {
       const incoming = new URL(request.url);
       return Response.redirect(
         "http://127.0.0.1:8000/api/v1/platforms/ebay/callback" + incoming.search,
         302,
       );
     },
   };
   ```

3. Deploy it, and note its address — something like `https://your-name.workers.dev`.

The redirect sends your browser to `127.0.0.1`, which always means "this computer", so it only
works from the computer StockSmith runs on.

## 4. Register the redirect with eBay

1. In the developer site, find the user-token settings for your production keyset ("Get a token
   from eBay via your application" or similar) and add a redirect.
2. Set its **accepted URL** (sometimes "auth accepted URL") to your Worker's address from step 3.
3. eBay gives the redirect a name — the **RuName**. It looks like a jumble of your name and
   letters, not like a web address. Copy it.

## 5. Enter the details in StockSmith

1. Go to **Settings › Stores & sync** and choose **eBay**.
2. In the **Developer app** card, set the environment to **Production**.
3. Open its credentials and enter the **Client ID** (App ID), **Client secret** (Cert ID) and
   **RuName** — the name, not the Worker's address.
4. Click **Save credentials**.

## 6. Connect your shop

1. At the top of the eBay store page, click **Connect**.
2. Your browser opens eBay. Sign in and agree.
3. eBay sends you through your redirect and the browser shows a "connected" page. Return to
   StockSmith.

## 7. Set up the signing key, for fees

eBay only reports the fees it charged on an order to apps that sign their requests. In the
**Developer app** card, click **Set up** next to the signing key. StockSmith creates the key with
eBay and stores it.

eBay hands over the private half of the key once and keeps no copy. **Replace key** makes a new
one; the old one can't be recovered.

## 8. Choose where order history starts

Under **Order sync**:

1. Set **Import orders from**. Leave it empty and StockSmith fetches your entire history. For a new
   install, choose today or the day you counted your stock.
2. Click **Save**, then **Preview**, then **Sync now**, then turn on **Automatic sync**.

## 9. Link your existing listings

Listings are matched to products by **SKU**.

Some older eBay listings — ones created in Seller Hub or older tools — can't be seen by the part
of eBay StockSmith uses for stock until they're **migrated**. The **Tools** and **Unlinked
listings** cards on the eBay store page find these and offer to migrate them.

> **Migration is one-way.** eBay doesn't let a migrated listing go back. It keeps selling as
> normal, but read eBay's own notes on listing migration first if you rely on Seller Hub
> features.

## If something goes wrong

- **The browser shows an error page after you agree on eBay** — usually the redirect. Check the
  Worker is deployed, its address is the accepted URL on eBay, and StockSmith is running.
- **eBay says the RuName is invalid** — make sure you entered the RuName itself, not the Worker's
  address, and that it belongs to the same production keyset as the Client ID.
- **"Missing or expired authorization state"** — click **Connect** again.
- **Orders show no eBay fees** — set up the signing key (step 7).
- **A banner asks you to reconnect eBay** — newer StockSmith features need permissions your
  connection didn't ask for. Click **Reconnect**.

Store connections aren't included in backups. After restoring onto another computer, connect
again — your keyset, redirect and RuName stay the same.
