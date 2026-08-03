# Listing adoption — eBay migration & Etsy linking

Covers the two ways a live marketplace listing can fail to be reachable by StockSmith's
stock sync, and the in-app flows for fixing each.

## The two problems

| | eBay | Etsy |
|---|---|---|
| Symptom | SKU check reports `not_found` even though the listing is live and has the right SKU | SKU check reports `not_found` because the listing carries a SKU StockSmith doesn't know (or none) |
| Cause | The listing is a *classic* (Seller Hub / Trading API) listing. eBay's Inventory API — which `build_listing_sku_index` uses — has no record of it at all until it's migrated | Nothing is wrong on Etsy's side; StockSmith's catalog is what's missing the link |
| Fix | Migrate it (`bulkMigrateListing`), then link | Write StockSmith's SKU onto the listing, then link |
| Reversible? | **No.** Migration is one-way | Yes — it's an ordinary listing edit |

Etsy has no classic/modern split: every Etsy listing is visible through one endpoint, so
a `not_found` there is always the second problem, never the first.

## Where the flows live

- **Per product**: Product → Platform sync tab → *Find unmigrated listing* (eBay) /
  *Find unlinked listing* (Etsy). Appears when that product's sync status is
  `not_found` or `partial`.
- **Shop-wide**: Settings → Integrations → the amber banner with the gap count →
  *Review*. Same picker, plus a step to choose which StockSmith product to link to.

## Before using this against a production shop

1. **Reconnect eBay once.** This feature needs eBay's base OAuth scope
   (`https://api.ebay.com/oauth/api_scope`) for its Trading API calls. Connections
   authorised before v0.4.0 don't have it — they keep syncing orders and pushing
   quantities perfectly well, which is why Settings surfaces an explicit
   "reconnect" banner rather than letting it fail at click time.

   *Known issue in v0.4.0, fixed in v0.4.1:* the banner appeared even after a successful
   reconnect and could not be cleared. eBay's token endpoint returns no `scope` field, so
   the granted scopes were never recorded, and the check read "not recorded" as "not
   granted". The scope check now fails open when scopes are unknown — it exists to turn a
   confusing 401 into a clear instruction, not to gate access, and eBay enforces the real
   thing regardless. A genuinely missing scope now surfaces on the API call itself, with
   the same reconnect guidance attached.
2. **Check Business Policies are on** (eBay Account → Site Preferences). Migration
   requires them; StockSmith can't detect this locally and will surface eBay's own
   rejection instead.
3. **Try one listing first.** Several request/response shapes in this feature have not
   been verified against a live eBay account — see *Unverified surfaces* below.

## SKU conflicts — StockSmith is the source of truth

If the marketplace SKU differs from StockSmith's computed SKU (`PARENT-SUFFIX`), the
StockSmith value always wins as the local lookup key. What happens on the marketplace
depends on the platform:

- **eBay**: with *Rewrite eBay's SKUs to match StockSmith* ticked (default), the SKUs are
  revised **before** migration, via `ReviseFixedPriceItem`. This is deliberate — a
  classic listing's SKU is freely editable, but once migrated the SKU becomes the
  resource identifier in the Inventory API's URL path and there is no documented rename;
  the only route would be create-new / repoint-offer / delete-old, and deleting an
  inventory item also deletes its offers and ends the live listing. Doing the alignment
  pre-migration avoids that entirely.
  Unticked, the mismatch is only reported. Quantity pushes for a mismatched unit will
  keep failing (visibly, in the listing-push log) until it's fixed in Seller Hub.
- **Etsy**: StockSmith's SKU is always written onto the listing as part of linking —
  there's no migration to carry a correct SKU across, so linking without writing would
  just re-break on the next sync check.

A product or variant with **no SKU** can't be linked on Etsy at all: there'd be nothing
to write, and blanking the listing's existing SKU would make things worse.

## Safety properties worth knowing

These are enforced in code and covered by tests (`test_listing_adoption_flow.py`,
`test_sku_alignment.py`, `test_etsy_unadopted_listings.py`):

- **Revise happens strictly before migrate.** Backwards would leave permanently
  mismatched SKUs with no in-app fix.
- **A failed migration writes nothing locally**, so a retry starts clean.
- **Re-running an adoption is safe.** An "already migrated" rejection is treated as
  success and the SKUs are read back — important because migration is irreversible, so a
  partially-failed adoption must be re-runnable.
- **Every variation is echoed back** on an eBay revise and an Etsy inventory write. Both
  APIs treat an omitted entry as a deletion, so a partial payload would destroy live
  variations silently.
- **Alignment is a no-op when SKUs already match** — it never edits a listing needlessly.
- **`Listing.external_listing_id` holds different things per platform** (eBay: the SKU;
  Etsy: the listing id), matching what each adapter's index writes. Swapping them would
  break every subsequent sync check and push.

## Trading API constraints

Two findings from `docs/plan-ebay-existing-store-onboarding.md` (branch
`docs/ebay-existing-store-onboarding`) shape this implementation and are easy to
regress:

- **Auth is not `Authorization: Bearer`.** The Trading API takes the same OAuth user
  token in `X-EBAY-API-IAF-TOKEN`, with `RequesterCredentials` omitted and no
  DevID/AppName/CertName needed — which is what makes it reachable with the credentials
  StockSmith already stores. Routing these calls through the adapter's shared
  `_request_once` helper would silently break every one of them; there's a test pinning
  this (`test_trading_auth_uses_iaf_token_not_bearer`).
- **The budget is 5,000 calls/day for the whole Trading API**, versus 2,000,000/day for
  the Inventory API — about 400× tighter. Short-duration limits are generous, so the
  daily total is the real constraint. Consequently every Trading call here is
  user-initiated: the shop-wide scan is a *Scan listings* button, not something that
  runs when Settings opens, and per-listing `GetItem` fires only when a user selects a
  specific listing.

## Unverified surfaces

Carried forward honestly rather than assumed working. Everything here is implemented to
eBay's published schemas but has **not** been exercised against a live account, and this
adapter has a track record of live testing surfacing shape surprises (see the comments
on `_parse_line_item` and `_net_money` in `platforms/ebay.py`):

- `GetMyeBaySelling`, `GetItem`, `GetUser`, `ReviseFixedPriceItem` — the whole Trading
  API (XML) surface, including whether it accepts a plain OAuth bearer token as used here.
- `bulkMigrateListing` request/response shape, and the exact wording eBay uses when
  rejecting an already-migrated listing (`_ALREADY_MIGRATED_MARKERS` matches loosely for
  this reason).
- Migration eligibility rules beyond "has a SKU" and listing type. StockSmith's local
  check is a best-effort pre-filter only — eBay's own rejection is authoritative, which
  is why ineligible rows are shown with reasons rather than hidden.

Because `GetMyeBaySelling`'s ActiveList doesn't reliably return the `<Variations>` block,
the list view never claims a listing has no SKU — it says "SKUs checked when you select
it" and fetches authoritative detail via `GetItem` on selection. Getting this wrong in
the other direction would grey out exactly the multi-variation listings the feature
exists to adopt.

## Not built

- **Post-migration SKU rename** — see above; needs verification that eBay supports it at
  all before any attempt.
- **Creating a StockSmith product from an Etsy listing.** The Etsy picker links to an
  *existing* product; if none is suitable, create it first, then link.
