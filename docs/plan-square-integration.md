# StockSmith — Square Integration (in-person custom orders): Planning Pass

## Status

Planning only — nothing here is implemented. The sandbox spike (2026-09-29,
`scripts/dev/square_sandbox_spike.py`) has confirmed the field names in the mapping table
below against a real sandbox order (custom patch + delivery line + pickup fulfilment, paid
with Square's test card). Two behaviours differ from what we assumed going in — see
"Spike findings" below.

**Update 2026-09-30:** phases 1–6 (order import) have since shipped in 0.21.0. A second
spike (`scripts/dev/square_catalogue_spike.py`) now revisits the "never push a listing to
Square" decision — see "Catalogue sync (spike 2026-09-30)" below. Nothing from that section
is built yet.

## Goal

Take in-person orders for custom items (e.g. a leather patch), capture the customisation
and collect/deliver choice, issue the customer a receipt, and see the order in StockSmith
next to Etsy/eBay orders — one place to know what to make, what each item should say, and
whether it's being collected or delivered.

## Reference docs

Square developer docs (Orders API):
- [How it works](https://developer.squareup.com/docs/orders-api/how-it-works) · [Search Orders](https://developer.squareup.com/docs/orders-api/manage-orders/search-orders) · [Create orders](https://developer.squareup.com/docs/orders-api/create-orders)
- [Manage order fulfilments](https://developer.squareup.com/docs/orders-api/fulfillments) (pickup / shipment / delivery / in-store; stored on `Order.fulfillments`) · [Fulfilment object](https://developer.squareup.com/reference/square/objects/Fulfillment)
- [Orders API reference](https://developer.squareup.com/reference/square/orders-api)

Square UK help centre:
- [Tax settings](https://squareup.com/help/gb/en/article/5061-create-and-manage-your-tax-settings) · [UK tax and invoice requirements](https://squareup.com/help/gb/en/article/7054-united-kingdom-tax-and-invoice-requirements)

(These were found via search only — Square's sites are blocked from the cloud session, so field names remain "verify".)

## Decisions so far

| Question | Answer |
|---|---|
| Store customer contact details in StockSmith? | **No** (revised) — Square already holds contact and delivery details; StockSmith stays consistent with its no-buyer-data default |
| Payment | **Paid in full up front** (orders arrive settled) |
| How orders are entered at the counter | **Undecided** — see options below |
| VAT | **Seller is not VAT-registered — VAT is out of scope for now.** Import tax fields as reported (expected £0) and revisit if that changes |
| Deliverable for this pass | This plan; no code |

## What already exists (and helps)

- Platform adapter pattern (`services/platforms/`: Etsy, eBay) with a normalised
  `ExternalOrder` / `ExternalOrderLine`. Square is a third adapter plus one registry branch.
- `OrderLine.variation_text` already holds per-line personalisation text (from Etsy).
  Square's per-item customisation maps straight onto it — no new column for the core need.
- `PaymentState.settled` gating: paid-up-front Square orders import cleanly.
- `ListingPlatform` enum, `order_sync` preview/commit flow, Settings > Integrations
  credentials, encrypted token storage.

## What's missing / must change

1. **No customer details.** Square is the system of record for name, email, phone and
   delivery address, and issues the receipt. StockSmith stores none of it, which keeps the
   existing privacy default intact for every platform (no exception in `order_sync`, no new
   personal-data columns, backups/exports unchanged). The order carries a Square order id so
   you can look the customer up in Square when needed.
2. **Fulfilment method** (collect vs deliver, plus collection date). No field today.
   Add `fulfilment_method` (collect | delivery) and `collect_by` (date, nullable) on `Order`.
3. **Delivery as a line item.** Delivery is a normal Square line item (e.g. "Delivery").
   StockSmith should recognise it (configurable SKU/name) so it isn't treated as an unmapped
   product, and can drive `fulfilment_method = delivery`.
4. **Ship-by / collect-by surfacing** in the orders list so custom jobs have a visible
   "due" date. `ship_by_date` exists; reuse it for collect-by rather than adding a second
   date if that reads well in the UI.
5. **Custom items and stock.** A bespoke patch may not be a stocked variant. Decide whether
   Square SKUs map to catalogue products (build-from-BOM as today) or are "made to order"
   lines with `needs_mapping` cleared — see open questions.
6. **Channel label.** New `ListingPlatform.square` value (or a `ManualOrderChannel`-style
   "in person" tag). Prefer `ListingPlatform.square` so uniqueness on
   `(platform, external_order_id)` and sync tooling work as for Etsy/eBay.

## How the data flows

```
Customer at counter
   -> Square (item + customisation note/modifier, Delivery or Pickup, customer record, payment, receipt)
   -> StockSmith SquareAdapter pulls orders (Orders API, SearchOrders; verify)
   -> normalised ExternalOrder -> existing order_sync -> Orders list
```

Square owns: taking payment, issuing the receipt to the customer's email/phone, the
customer directory. StockSmith owns: what to make, stock/allocation, fulfilment tracking,
cost/profit.

## Mapping Square -> StockSmith (confirmed against sandbox 2026-09-29)

| Square | StockSmith |
|---|---|
| Order `id` | `external_order_id` |
| Line item `note` (plain string, confirmed — no modifier was needed) | `OrderLine.variation_text` (customisation) |
| Line item `catalog_object_id` / variation SKU | `OrderLine.sku` (match via existing SKU lookup) — **not exercised by the spike** (the test order used ad-hoc line items with no catalog object), but confirmed as the intended model: see decision below |
| Line item named e.g. "Delivery" | order-level delivery flag; not a product line (confirmed shape: plain line item, `name: "Delivery"`, no special type) |
| Order `fulfillments[].type` `PICKUP` / `SHIPMENT` (both confirmed) | `fulfilment_method` |
| Order `fulfillments[].pickup_details.pickup_at` (confirmed) | `collect_by` — no `expires_at` was present with `schedule_type: SCHEDULED`; that field only appears for `schedule_type: ASAP` per docs, not re-verified here |
| Order `fulfillments[].shipment_details.recipient.address` (confirmed) | not imported — delivery address stays in Square, same as other customer contact details |
| Order `customer_id` | not imported (customer stays in Square) — spike order had no customer attached, so this wasn't exercised |
| Order/line-item `total_tax_money` (confirmed; was £0 as expected — no tax rate configured, not VAT-registered) | `tax_charged` |
| Order `total_money`, `net_amount_due_money` | `grand_total`, and **settlement check** — see spike finding below, not `order.state` |
| Payment `processing_fee[].amount_money` (Payments API `GetPayment`; confirmed, see spike finding below) | `payment_fees` / `payment_net` |
| Order `state` `CANCELED` (confirmed) | `is_cancelled` (feeds existing pending-cancellation flow) |
| Order `updated_at` (confirmed — advances when payment is applied) | `last_modified` (sync watermark) |

### Spike findings (things that differed from assumption)

1. **Settlement is not `order.state`.** The order's `state` stayed `"OPEN"` even after being
   paid in full via the Payments API — it never flipped to `"COMPLETED"`. The reliable signal
   that an order is paid in full is `net_amount_due_money.amount == 0` (it was the full order
   total before payment, `0` after). `PaymentState.settled` should be derived from
   `net_amount_due_money`, not `order.state`.
2. **Processing fee is not on the payment returned from creating it.** The `Payment` object
   returned immediately after `POST /payments` has no `processing_fee` field. A follow-up
   `GET /payments/{id}` — even a couple of seconds later — returns
   `processing_fee: [{ type: "INITIAL", amount_money: {...}, effective_at: <next day> }]`.
   The `effective_at` timestamp is the next day even though the fee amount was already
   available, which matches Square's documented "calculated after settlement" behaviour, but
   in sandbox at least it's readable well before then. **Consequence:** the sync can't take
   the fee from the payment-creation call; it needs a follow-up read (either immediately, or
   as a deferred re-check) before it can populate `payment_fees`.
3. **Cancelling an order requires cancelling its fulfilment in the same call.**
   `PUT /orders/{id}` with `state: CANCELED` alone is rejected (`400 INVALID_VALUE`) unless
   every fulfilment on the order is also moved to a terminal state
   (`CANCELED`/`COMPLETED`/`FAILED`) in that same request. Done correctly, `order.state`
   reliably flips to `"CANCELED"` — unlike settlement, cancellation is one thing `order.state`
   is trustworthy for.
4. **A cancelled order is not "settled".** The cancelled test order (never paid) still shows
   `net_amount_due_money.amount: 1500` — nonzero, same as any unpaid order. So the settlement
   check (finding 1) must only be evaluated when `order.state != "CANCELED"`, otherwise a
   cancelled order looks indistinguishable from one still awaiting payment.
5. **Shipment fulfilments carry no date field.** `fulfillments[].shipment_details` has
   `recipient.address` but nothing equivalent to pickup's `pickup_at` — no ship-by or expected
   ship date in this flow at all. Reusing one date field (`ship_by_date`/`collect_by`) for both
   fulfilment types only works for `PICKUP`; a `SHIPMENT` order would need a
   StockSmith-derived date (e.g. days from order date) rather than one read from Square, if a
   "ship by" date is wanted for delivery orders too.
6. **Catalogue SKU resolution confirmed.** A real catalogue item (an `ITEM` with an
   `ITEM_VARIATION` carrying `item_variation_data.sku`), ordered via `catalog_object_id`
   rather than an ad-hoc line — the line item itself still has no `sku` field, but
   `POST /catalog/batch-retrieve` on that id returns `item_variation_data.sku` exactly as
   expected. `SquareAdapter._resolve_skus` is confirmed correct as written.
7. **Square blocks cancelling a paid order outright — refunding first doesn't unblock it.**
   `PUT /orders/{id}` with `state: CANCELED` on an order with a processed payment is rejected
   every time (`"Orders cannot be canceled after payments have been processed"`), even after
   issuing a refund via `POST /refunds` first. This is a hard rule, not a "cancel needs a
   refund first" workflow — see the "Refunds/cancellations" open question for what this means
   for StockSmith's write-back policy.
8. **A refunded order is indistinguishable from a settled one at the order level.** After
   refunding a paid order's payment, the *order* still shows `state: "OPEN"` and
   `net_amount_due_money.amount: 0` — identical to normal settlement. The refund only shows up
   on the *payment*: `GET /payments/{id}` returns `refunded_money: { amount: <refunded>,
   currency }` and a new `refund_ids` array, while `status` stays `"COMPLETED"` throughout.
   The order's own `refunds` field never populated in this sandbox run (even checked
   immediately after a `PENDING`-status refund) — not relied upon.
   **Consequence:** `PaymentState.reversed` cannot be derived from the order alone the way
   Etsy/eBay derive it from their list response; it needs the same per-order payment fetch as
   the processing fee, which `SquareAdapter` now does together in one call
   (`_fetch_payment_details`) rather than the fee-only version built before this was found.

## Capturing the details: options for the counter

**A. Square POS (phone/tablet/reader), you type it in.** Customisation as an item note or a
modifier ("Text on patch"), customer attached via Square's customer picker, fulfilment set
to Pickup or a "Delivery" item added. Least set-up; relies on you entering things
consistently. Modifiers with a required text/choice make consistency better.

**B. Customer completes it themselves** (Square Online store, payment link, or a Square
Checkout form). Customer types their own patch text and contact details; fewer typos and
less typing for you. More set-up, and the item must exist as an online product with
custom-text fields.

Either works with the same StockSmith adapter — the choice only affects how the data
arrives in Square. **Recommendation:** start with A, keeping the StockSmith side agnostic,
and revisit B if typing at the stall becomes a bottleneck.

## Sync approach

- Reuse the existing pull-sync (preview + commit) via a `SquareAdapter`; poll on the same
  scheduler as Etsy/eBay. Filter by `updated_at` against the watermark.
- Optional later: Square webhooks (`order.created`/`order.updated`) for near-real-time.
  Needs a publicly reachable URL, which a desktop install doesn't have — so polling first.
- Auth: Square OAuth (production) with a sandbox environment for testing (mirrors eBay's
  sandbox/production `PlatformEnvironment`). Scopes likely `ORDERS_READ`,
  `PAYMENTS_READ`, `ITEMS_READ` (verify). A personal access token is a simpler first step
  for a single-seller app.
- Square locations: an order belongs to a location; pick the relevant location(s) in
  Settings.

## Phased plan

1. ~~**Spike (sandbox):**~~ **Done 2026-09-29.** Square developer account, sandbox seller, test
   orders covering a paid pickup order, a paid shipment order, and a cancelled order; read back
   with SearchOrders and GetPayment. See "Spike findings" above.
2. ~~**Schema:**~~ **Done 2026-09-29** (commit `fb23ed1`). `ListingPlatform.square`,
   `orders.fulfilment_method`, `orders.collect_by`. Migration verified to apply/roll back
   cleanly; existing order/listing/platform tests (398) unaffected.
3. ~~**Connect Square (minimal settings).**~~ **Done 2026-09-29** (commit `366490d`) —
   backend only, no frontend UI yet:
   - A pasted Square personal access token (simpler than OAuth for a single-seller app),
     stored through the existing encrypted `platform_connections` machinery, keyed to
     `ListingPlatform.square`. `PlatformConnection.is_connected` needed a Square-specific
     branch since it has no refresh token to key off (Etsy/eBay's stricter check is
     unchanged).
   - Sandbox vs. production reuses the existing `PlatformEnvironment` split (mirrors eBay).
   - Location: `POST /platforms/square/connect` validates the token and returns the
     account's locations; `POST /platforms/square/location` stores the chosen one in
     `external_account_id` (the same field Etsy/eBay use for "which account"), resetting the
     sync watermark on a change, same as an Etsy shop change. `GET /platforms/square/locations`
     lets the location be changed later without re-pasting the token.
   - New `app/services/platforms/square_client.py` holds just enough Square REST access
     (list locations) to support connecting — not the full adapter, which is next.
   - Full OAuth (if ever needed for a public listing on Square's app marketplace) remains
     explicitly deferred.
   - **Still needed:** a Settings UI card (paste-token form + location picker) — currently
     only reachable via the API. Deferred to alongside phase 6's UI work, or sooner if you'd
     like to test this via the app rather than API calls.
4. ~~**`SquareAdapter`**~~ **Done 2026-09-29** (`app/services/platforms/square.py` +
   `square_client.py`'s new `search_orders`/`get_payment`/`batch_retrieve_catalog_objects`;
   `ExternalOrder` gained `fulfilment_method`/`collect_by` fields, and `order_sync._apply_financials`
   now writes them onto `Order`, same as `ship_by_date`). 13 new adapter tests, all passing,
   plus the full existing suite (1,257 tests) unaffected. Only `fetch_orders_since` is
   implemented — every other `PlatformAdapter` Protocol method (OAuth, listing push/drafts)
   raises `NotImplementedError` with an explanation, since Square doesn't do either (see the
   module's own docstring). **Not yet wired into `get_adapter()`/the scheduler** — that's
   step 5, next.
   - Settlement from `net_amount_due_money == 0`, cancellation checked first (spike findings
     1 and 4) — confirmed and tested.
   - `fulfillments[].type` -> `fulfilment_method`; pickup's `pickup_at` -> `collect_by`;
     shipment leaves `collect_by` NULL — confirmed and tested.
   - The "Delivery" line is excluded from `ExternalOrder.lines` by a hardcoded
     case-insensitive name match (`_DELIVERY_LINE_NAME` in `square.py`) — becomes a Settings
     field in phase 5/6, not before.
   - Processing fee: a follow-up `GetPayment` call, gated the same way Etsy/eBay skip
     re-enrichment for an unsettled or already-synced order — confirmed and tested.
   - **Both things flagged as unverified when this adapter was first built have since been
     checked against a real sandbox run (2026-09-29, second spike run) and fixed:**
     1. **SKU resolution — confirmed correct as written.** A real catalogue item
        (`ITEM_VARIATION` with `item_variation_data.sku`), ordered via `catalog_object_id`,
        round-trips through `batch-retrieve` exactly as the adapter assumed. No code change
        needed.
     2. **Refunded-order detection — was wrong, now fixed (spike findings 7-8).** The
        original guess ("a cancelled order with a tender means reversed") turned out to be
        both unreachable (Square blocks cancelling a paid order outright, confirmed live)
        and wrong for the case that actually matters: a *refunded, not cancelled* order looks
        identical to a settled one at the order level (`state: "OPEN"`,
        `net_amount_due_money: 0`) — only `payment.refunded_money` reveals the refund.
        `SquareAdapter` now fetches payment details (fee + refund status together, one call)
        whenever an order looks paid, not only when it looks cancelled; see
        `_fetch_payment_details`. 13 adapter tests (was 12), including two new ones covering
        this directly.
   - Left unset rather than guessed: `subtotal` — Square's order response has no explicit
     subtotal field, and deriving one from `total_money` and the other totals would mean
     assuming a formula never checked against a real order with both tax and a discount.
5. ~~**Registry + sync wiring**~~ **Done 2026-09-29.** `get_adapter()` now returns a
   `SquareAdapter` — skipping the client id/secret lookup entirely, since Square has none
   (a pasted personal access token, not OAuth). `sync_scheduler` gained a Square lock and
   starts a Square auto-sync loop alongside Etsy/eBay's, with one guard: the piggybacked
   shipping-price refresh is skipped for Square (it has no shipping-profile integration at
   all — nothing to link a postage price to), which would otherwise raise
   `NotConnectedError` every cycle. `platform_api_usage` gained an explicit (conservative,
   not provider-confirmed the way Etsy/eBay's figures are — Square publishes per-second
   burst limits, not a daily cap) budget entry, and `square_client` now records every call
   against it, same as the other two adapters. Also added Square to
   `sync_status._SUMMARISED_PLATFORMS` and `notification_scheduler`'s API-usage-alert sweep,
   so its sync health and usage alerts are visible even before a Settings UI exists for it.
   5 new tests confirming the wiring itself (adapter resolution, environment fallback,
   scheduler inclusion, the shipping-refresh skip) plus the full suite unaffected.

   **What this doesn't do:** `_PUSH_ENABLED_PLATFORMS`/`listing_reconcile`'s platform tuples
   and eBay/Etsy's shipping-profile-linking routes deliberately still exclude Square — it
   has no listings to push and no shipping profiles to link, so those stay untouched by
   design, not by oversight.
   Manual sync already works end-to-end through the existing generic
   `POST /platforms/square/preview-sync` / `/sync-orders` endpoints (no new endpoint needed
   — they're keyed by the `{platform}` path parameter and already call `get_adapter`), but
   there's still no Settings UI card to turn on Square's auto-sync toggle or trigger a
   manual sync from — only via direct API calls, same caveat as the connect step.
6. ~~**UI**~~ **Done 2026-09-29** — verified end-to-end in a browser against a real scratch
   backend + the real Square sandbox (not just type-checked), not only settings but the
   piece the plan originally scoped to phase 6:
   - **Connect Square**, held back at step 3 for lack of a UI, is now built: a
     `SquareConnectDialog` (paste-token + environment, then a location picker) reached from
     both the Stores hub card and the store's own page; a `SquareLocationCard` lets the
     location be changed later without re-pasting the token (`GET /platforms/square/locations`).
     Manually confirmed live: a bad token surfaces Square's real rejection
     ("Square rejected this access token") all the way through the dialog.
   - Square's store page shows only what applies to it (connection header, location,
     order sync/preview/log) — the listing-push, listing-profiles, tools, and OAuth
     developer-app sections are hidden for it, since none of those concepts exist for
     Square (see the "custom items vs catalogue" decision — Square sells ordinary
     StockSmith catalogue products, but never pushes a listing to Square itself).
     **Being revisited** — see "Catalogue sync (spike 2026-09-30)".
   - **Orders list/detail:** `Order` gained `fulfilment_method`/`collect_by` in both the
     backend response schema (`OrderRead` — these existed on the model since step 2 but were
     never actually returned by the API until now) and the frontend type. A shared
     `effectiveDueDate()` helper (collect_by for a collect order, ship_by_date otherwise)
     replaced the ship_by_date-only due-date logic in both the list and detail view, per the
     earlier merged-column decision. A small "Collect"/"Delivery" tag appears next to the
     platform badge in both places. Customisation text and the Square order reference needed
     no changes at all — both already rendered generically (`variation_text`,
     `external_order_id`).
   - Not built: the "ready to collect" filter (optional per the original plan, skipped for
     now) and a configurable delivery-line-item name (still hardcoded in the adapter, as
     phase 4 already flagged).
7. **Optional, not scheduled:** Square webhooks for near-real-time sync (needs a public URL —
   out of reach for a desktop install, so no timeline); write-back (marking a Square
   fulfilment complete from StockSmith) — decided against for now (see open question 3);
   fulfilment-aware BOM/shipping-profile switching (see open question 1's future
   consideration).

**Suggested build order:** 3 -> 4 -> 5 -> 6, i.e. get a real Square credential and location
connected first (so the adapter can be exercised against your actual sandbox as it's built,
not just fixtures), then the adapter itself with tests, then wire it into the scheduler, then
surface it in the UI last. Each of 3-6 is a natural point to check in before moving to the
next.

## Catalogue sync (spike 2026-09-30)

Planning only — nothing here is built. Goal: treat the Square catalogue the way Etsy's is
treated today — push a product from StockSmith, check a product's listing against Square
and correct its quantity, and keep quantities in step automatically. **Orders stay
one-way**: Square remains the source of truth for orders, and nothing here writes order
status, cancellations or refunds back (see open questions 2 and 3).

This reverses the earlier "Square never gets a pushed listing" position (phase 6 and open
question 1). The Square product is still an ordinary StockSmith catalogue `Product` — the
change is that StockSmith can now *create* it and *keep its quantity* in Square, rather than
relying on it being entered by hand.

### Etsy functions and their Square equivalents

| # | Etsy function today | Square equivalent | Feasibility | Recommendation |
|---|---|---|---|---|
| 1 | Listing SKU index (`build_listing_sku_index`) | `SearchCatalogItems` with `archived_state: ARCHIVED_STATE_ALL` + `BatchRetrieveInventoryCounts` for the chosen location | High | **Must** — everything below depends on it |
| 2 | Per-product check sync (`/check-sync`) | Generic once #1 exists | High | **Must** |
| 3 | Push corrections (`push_listing_quantity`) | `BatchChangeInventory` `PHYSICAL_COUNT`, ≤100 changes per call, dated to the sales-imported watermark (finding C2) | High | **Must** |
| 4 | Check all listings in bulk | Generic once #1 exists | High | **Must** |
| 5 | Event-driven push + hourly reconcile | Add Square to `listing_push._PUSH_ENABLED_PLATFORMS` and `listing_reconcile._PLATFORMS` | High | **Must**, after #3 |
| 6 | Create a listing (`create_draft_listing`) | `UpsertCatalogObject` (item + variations, idempotency key), created **archived**; images via `CreateCatalogImage` | Medium — no draft state (finding C1) | **Must** |
| 7 | Draft readiness | New Square rule set: name, fixed price (or variable pricing), a SKU per unit | High | **Must**, with #6 |
| 8 | Adopt existing listings | Write SKUs onto existing Square variations; read-then-upsert with `version` | High | **Should** — for items entered by hand at the counter |
| 9 | Platform limits, catalogue compatibility, variant conflicts | Add Square to `platform_limits._DEFAULT_LIMITS` | High | **Should** |
| 10 | Platform fee components | Square fee rows via a migration — `seed._ensure_platform_fee_components` returns early if any row exists, so seeding won't reach existing installs | High | **Should** |
| 11 | Push log, sync health, API usage | Already generic | High | Free |
| – | Per-platform listing copy, backfill, listing profiles, shipping-profile linking, ongoing price sync | Etsy-specific, low value, or not done for Etsy either | – | Skip |
| – | Order status / cancel / refund write-back | – | – | **Out of scope** |

Square limits used above (from Square's object reference, not re-verified unless noted in
the findings): item name 512, variation name 255, `description_html` 65,535, 250 variations
per item, 6 item options per item, SKU 255 (**confirmed**, finding C3).

### Findings (confirmed against sandbox)

Run twice on 2026-09-30 with `scripts/dev/square_catalogue_spike.py`; full responses are
written to `square_catalogue_spike_output.json`.

- **C1. Archiving works as a stand-in for a draft.**
  - An item can be created with `is_archived: true`, have stock set while archived, and be
    un-archived later by an upsert.
  - Archiving only hides the item from the till: an API order for an archived variation was
    still accepted and paid. That's harmless here, because counter orders come through the
    till.
  - `SearchCatalogItems` **excludes archived items by default**. The index must pass
    `ARCHIVED_STATE_ALL`, or a just-pushed archived item reads as "not found". `ListCatalog`
    does include archived items.
  - The search index lags writes by a few seconds. An immediate search after creating an
    item can return nothing.
  - **Versioning is enforced per field.** An upsert with a stale `version` that changes a
    field modified in the meantime is rejected (`VERSION_MISMATCH`, naming the field and
    both values). Resending unchanged content with a stale version is accepted. Omitting
    `version` on an existing object is rejected.
  - So every update is read-then-upsert, and a clash with a Dashboard edit comes back as a
    clear error rather than a silent overwrite.
- **C2. Backdated physical counts have later sales re-applied on top.**
  - The test ran: count 10 dated 3h ago, then a paid sale of 2, leaving 8.
  - A count of 10 dated 1h ago (before the sale) reads back **8**: the sale is re-applied on
    top.
  - A count of 10 dated *now* reads back **10**: the sale is lost. This is the race a naive
    "set absolute quantity" push would have.
  - Counts dated more than 24h ago are rejected (`INVALID_TIME`, "cannot set history older
    than 24h0m0s"). 23h50m was accepted.
  - **Design consequence:** date each `PHYSICAL_COUNT` to the point up to which StockSmith
    has imported all Square sales, not to "now". If that point is more than 24h old
    (e.g. order sync has been failing), skip the push and flag it rather than pushing a
    count dated now.
  - Always send `ignore_unchanged_counts: false`. The default skips a count equal to the
    previous count, which here would drop a legitimate correction.
- **C3. SKUs: duplicates allowed, 255-character limit.**
  - Two items with the same SKU were both accepted, and an exact SKU search returns both.
    The index must report a duplicated SKU as a conflict, not pick one.
  - The SKU limit is exactly 255: 255 accepted, 256 rejected ("longer than max length 255").
  - Looser than Etsy (32) or eBay (50), so Square never becomes the strictest SKU limit.
- **C4. Overselling drives stock negative.**
  - Selling 3 with 1 in stock via a paid API order was accepted, and the count went to
    **−2**.
  - `location_overrides[].sold_out` was `true` on the first run but still absent on the
    second run's immediate read. Square sets it asynchronously, so don't rely on it.
  - A later StockSmith push corrects the negative count. Whether the till app itself blocks
    or warns on a sold-out item is **not** covered by the API test — check on a real device.

### Still unverified

- Whether a count backdated *earlier* than an existing later count is ignored. The
  watermark-dated pushes should only move forward in time, but confirm before relying on it.
  Relatedly, how Square orders two counts with the same `occurred_at` (several pushes within
  one sync interval would share one).
- Image upload (`CreateCatalogImage`, multipart) — not exercised.
- 429 behaviour. Square publishes no daily cap. `square_client._request` currently turns a
  429 into a plain `PlatformSyncError` with no retry/backoff.
- Whether Square Online (same catalogue) exposes pushed items automatically.

### Code that currently assumes Etsy/eBay only

- `SquareAdapter`'s `push_listing_quantity` / `create_draft_listing` /
  `build_listing_sku_index` raise `NotImplementedError`, and its module docstring states the
  old decision.
- `listing_push._PUSH_ENABLED_PLATFORMS`, `listing_reconcile._PLATFORMS`.
- `draft_listing` / `draft_readiness` branch Etsy-else-eBay, so Square would silently fall
  into the eBay branch.
- `platform_limits._DEFAULT_LIMITS` has no Square entry.
- The Square store page hides the listing-push sections (phase 6).

### Open decisions

1. **Created items: archived (review in Dashboard, then un-archive) or live on the till?**
   Recommendation: archived, matching the Etsy rule that StockSmith never publishes.
2. May a push switch on `track_inventory` for a variation that has it off, or only report it?
3. Is Square Online in use? It shares the catalogue.
4. Are there items already in Square entered by hand? If so, adoption (#8) moves up.

## Open questions

1. ~~**Custom items vs catalogue.**~~ **Decided:** a Square product is a catalogue `Product`
   synced from StockSmith the same way as Etsy/eBay — SKU, quantity, BOM. E.g. "Bag Scouting
   Patch" is built from a leather patch blank + UV ink + cello bag, same as today. The
   customisation text (`note`) rides alongside on the order line; it doesn't change what's
   allocated from stock.

   **Future consideration (not blocking this pass):** collection vs delivery should be able to
   change *what* gets consumed and *which* shipping profile applies — e.g. a collected order
   skips packaging/kitting BOM items (no cello bag/mailer needed if it's handed over the
   counter) and uses a "Collection" shipping profile instead of a postal one. Today's BOM/
   shipping-profile model is per-product, not per-fulfilment-method, so this would need either
   a fulfilment-aware BOM variant or a post-allocation adjustment step. Revisit once the base
   adapter works.
2. **Refunds/cancellations. Decided: no write-back from StockSmith, same as Etsy/eBay.**
   Handled only via Square; StockSmith picks up the resulting `CANCELED` state on next sync
   and routes it through the existing pending-cancellation flow. Etsy has no seller-initiated
   cancel/refund endpoint at all, so its one-way policy isn't a choice; eBay and Square could
   technically support a write-back, but StockSmith treats the marketplace as the system of
   record for anything that moves money or touches the buyer relationship (refund amount,
   buyer notification, dispute handling) on principle, not just API availability.

   A second sandbox check (2026-09-29) confirms this is the right call for Square
   specifically, not just consistent with the others: **Square's Orders API refuses to
   cancel an order once a payment has been processed** (`"Orders cannot be canceled after
   payments have been processed"`, confirmed live — even after issuing a refund first). The
   only real lever on a paid order is a refund (a payment action, not an order action), and
   the order itself stays open in Square's system afterward rather than becoming
   `CANCELED` — so a "Cancel in StockSmith" button couldn't actually cancel the paid orders
   StockSmith would ever hold anyway (unpaid ones are never imported). This also means the
   `PaymentState.reversed` heuristic in `SquareAdapter._payment_state` (a cancelled order
   with a tender recorded) is unreachable in practice — see the adapter fix below.

   Cancellation shape for an unpaid order (the only kind that actually can become
   `CANCELED`) is confirmed by the spike (finding 3) — cancelling requires the fulfilment to
   be cancelled in the same call, and `order.state` reliably becomes `"CANCELED"` once
   that's done. What a *refunded-but-not-cancelled* paid order looks like (its order/payment
   fields) was being checked in the second sandbox run alongside this decision.
3. **Marking collected. Decided: one-way (Square -> StockSmith), no write-back** — matches how
   Etsy/eBay work today. Revisit if double-handling ("mark collected" in both places) becomes a
   hassle.
4. **Receipts.** Square emails/texts the receipt if a customer is attached and receipts are
   enabled in your Square settings — StockSmith does not generate one (it has no contact
   details to send it to). Just confirm your Square account has this switched on as wanted.
5. **Tax.** Effectively resolved — not VAT-registered, so `total_tax_money` comes through as
   £0 and is stored as-is (confirmed by spike). Revisit with an accountant if VAT status changes.
6. **Locations. Decided: single location** — can be preselected in Settings rather than needing
   a location picker. Processing fees: confirmed available via a follow-up `GetPayment` call
   (see spike findings), will feed `payment_fees` for profit reporting as planned.
7. **Region.** Resolved by spike — GBP, and Square returns a single `total_tax_money` field
   regardless of region (no separate `vat_charged`). Revisit only if a non-UK Square account is
   ever added.

## Risks

- Free-text customisation quality (typos, missing notes) if entered by hand at the counter.
- Processing fee isn't available at the moment of payment (see spike finding 2) — sync needs
  a follow-up read of the payment, adding complexity/timing to when `payment_fees` is known.
- Shipment fulfilments have no date field to hang a "ship by" surfacing on (see spike finding
  5) — a delivery due-date would need to be StockSmith-derived, not read from Square.

## Sales tax: Square vs Etsy/eBay

StockSmith does not calculate or remit tax itself: it records `tax_charged`/`vat_charged` and
`grand_total` exactly as the platform reports them. So the question is who collects and pays
the tax, which differs by channel (verify for your region and tax status):

- **Etsy/eBay:** in many regions they act as a marketplace facilitator and collect/remit some
  taxes on your behalf, so those amounts are deducted before you're paid.
- **Square:** does **not** do this. It can calculate tax on a sale (using tax rates you set up)
  and show it on the receipt, but the money lands with you and **you** are responsible for
  reporting and paying it. Square takes only its processing fee.

Consequence for StockSmith: import Square's tax as `tax_charged`/`vat_charged`, and treat
`grand_total` minus tax as revenue for profit reporting, so Square sales aren't overstated.
Whether that matches how Etsy/eBay orders are treated today needs checking in the profit
calculations before building. Tax setup (rates, VAT registration) is one to confirm with an
accountant.

## Fees comparison (UK, not VAT-registered)

On an Etsy order the seller's earnings screen shows buyer-paid amount minus Etsy's transaction,
payment processing and regulatory operating fees, plus VAT charged on those fees. All of it is
deducted by Etsy before payout — nothing further is paid to Etsy afterwards. Square works the
same way for its own charge: its processing fee is taken from the payout. Neither needs a
separate payment. Income tax on overall profit is separate from both (see accountant).
StockSmith should import Square's processing fee as `payment_fees` so profit matches how Etsy
orders are reported.
