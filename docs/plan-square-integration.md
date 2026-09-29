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
| Line item `catalog_object_id` / variation SKU | `OrderLine.sku` (match via existing SKU lookup) — **not exercised by the spike**, the test order used ad-hoc line items with no catalog object; still open, see open question 1 |
| Line item named e.g. "Delivery" | order-level delivery flag; not a product line (confirmed shape: plain line item, `name: "Delivery"`, no special type) |
| Order `fulfillments[].type` `PICKUP` (confirmed; `SHIPMENT` not tested) | `fulfilment_method` |
| Order `fulfillments[].pickup_details.pickup_at` (confirmed) | `collect_by` — no `expires_at` was present with `schedule_type: SCHEDULED`; that field only appears for `schedule_type: ASAP` per docs, not re-verified here |
| Order `customer_id` | not imported (customer stays in Square) — spike order had no customer attached, so this wasn't exercised |
| Order/line-item `total_tax_money` (confirmed; was £0 as expected — no tax rate configured, not VAT-registered) | `tax_charged` |
| Order `total_money`, `net_amount_due_money` | `grand_total`, and **settlement check** — see spike finding below, not `order.state` |
| Payment `processing_fee[].amount_money` (Payments API `GetPayment`; confirmed, see spike finding below) | `payment_fees` / `payment_net` |
| Order `state` `CANCELED` (not tested this spike) | `is_cancelled` (feeds existing pending-cancellation flow) — still to verify |
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
   order with a note, delivery line and pickup fulfilment, paid via Payments API, read back
   with SearchOrders and GetPayment. See "Spike findings" above.
2. **Schema:** migration for `fulfilment_method`, `collect_by`
   (if not reusing `ship_by_date`), `ListingPlatform.square`.
3. **Adapter + sync:** `SquareAdapter`, registry branch, delivery-line handling, tests mirroring the Etsy/eBay ones.
4. **UI:** Orders list/detail show customisation text, collect vs delivery, collect-by date,
   Square order reference; filter "to make" / "ready to collect".
5. **Settings:** connect Square, choose location, name of the delivery line item.
6. **Optional:** webhooks; write-back (mark Square fulfilment complete when collected).

## Open questions

1. **Custom items vs catalogue.** Is a leather patch a standard product with a customisation
   field (fits today's model), or a one-off with no catalogue entry? This decides whether
   stock/BOM allocation applies or lines are "make to order".
2. **Refunds/cancellations.** Handle only via Square, surfaced in StockSmith through the
   existing pending-cancellation flow?
3. **Marking collected.** Should collecting in StockSmith update the Square order, or stay
   one-way (Square -> StockSmith)?
4. **Receipts.** Square emails/texts the receipt if a customer is attached and receipts are
   enabled — confirm that's the receipt you want, rather than StockSmith generating one.
5. **Tax.** See "Sales tax" below.
6. **Locations / fees.** One Square location, or several? Should Square processing fees be
   recorded for profit reporting?
7. **Region.** Currency and tax type (`vat_charged` vs `tax_charged`) depend on your country's Square account.

## Risks

- Free-text customisation quality (typos, missing notes) if entered by hand at the counter.
- Processing fee isn't available at the moment of payment (see spike finding 2) — sync needs
  a follow-up read of the payment, adding complexity/timing to when `payment_fees` is known.
- Cancelled-order shape (`state: CANCELED`) and `SHIPMENT` fulfilments weren't exercised by
  the spike — still to confirm before relying on them.

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
