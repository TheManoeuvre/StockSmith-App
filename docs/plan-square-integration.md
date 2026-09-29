# StockSmith — Square Integration (in-person custom orders): Planning Pass

## Status

Planning only — nothing here is implemented. The sandbox spike (2026-09-29,
`scripts/dev/square_sandbox_spike.py`) has confirmed the field names in the mapping table
below against a real sandbox order (custom patch + delivery line + pickup fulfilment, paid
with Square's test card). Two behaviours differ from what we assumed going in — see
"Spike findings" below.

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
   now writes them onto `Order`, same as `ship_by_date`). 12 new adapter tests, all passing,
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
   - **Two things this adapter does that the spike never actually verified — flagged in the
     code, not silently assumed correct:**
     1. **SKU resolution.** A line item has no `sku` field itself — only `catalog_object_id`
        — so the adapter calls Square's Catalog API (`batch-retrieve`) to read
        `item_variation_data.sku`. The sandbox spike's test orders used ad-hoc line items
        with no catalog object at all, so this has never been exercised against a real
        Square catalogue item. **Needs a follow-up sandbox check** (create a Catalog item,
        order it, confirm the response shape) before this can be trusted in production.
     2. **A cancelled-and-previously-paid order's payment state.** The spike's cancelled
        test order was never paid. The adapter guesses `PaymentState.reversed` when a
        cancelled order has a tender recorded (Square requires refunding a paid order
        before/while cancelling it) — untested against a real paid-then-cancelled sandbox
        order.
   - Also left unset rather than guessed: `subtotal` — Square's order response has no
     explicit subtotal field, and deriving one from `total_money` and the other totals would
     mean assuming a formula never checked against a real order with both tax and a discount.
5. **Registry + sync wiring:** one branch in `app/services/platforms/__init__.py`'s
   `get_adapter()` (the project's adapter factory — explicitly designed so a new marketplace
   is "additive only" here); a `square` entry in the sync scheduler; a rate-limit budget entry
   in `platform_api_usage.py` (Square's own published limits — to check, not yet looked up).
6. **UI:** Orders list gets a collect/delivery indicator, and shows one merged "due" date
   column/sort — `collect_by` for a collect order, `ship_by_date` for everything else —
   rather than two separate date columns, so the list keeps one urgency ordering across every
   platform (**decided**). Order detail shows the customisation text (reuses the existing
   Etsy variation-text display), the Square order reference, and collect vs. delivery.
   Optional filter for "ready to collect".
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
2. **Refunds/cancellations.** Handled only via Square; StockSmith picks up the resulting
   `CANCELED` state on next sync and routes it through the existing pending-cancellation flow,
   same as Etsy/eBay. No write-back. Shape confirmed by spike (see finding 3) — cancelling
   requires the fulfilment to be cancelled in the same call, and `order.state` reliably becomes
   `"CANCELED"` once that's done.
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
