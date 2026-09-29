# StockSmith — Square Integration (in-person custom orders): Planning Pass

## Status

Planning only — nothing here is implemented. Square API details below are from general
knowledge and **must be verified against Square's current docs/sandbox before building**
(marked "verify").

## Goal

Take in-person orders for custom items (e.g. a leather patch), capture the customisation
and collect/deliver choice, issue the customer a receipt, and see the order in StockSmith
next to Etsy/eBay orders — one place to know what to make, what each item should say, and
whether it's being collected or delivered.

## Decisions so far

| Question | Answer |
|---|---|
| Store customer contact details in StockSmith? | **No** (revised) — Square already holds contact and delivery details; StockSmith stays consistent with its no-buyer-data default |
| Payment | **Paid in full up front** (orders arrive settled) |
| How orders are entered at the counter | **Undecided** — see options below |
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

## Mapping Square -> StockSmith (verify all field names)

| Square | StockSmith |
|---|---|
| Order `id` | `external_order_id` |
| Order `line_items[].note` or modifier text | `OrderLine.variation_text` (customisation) |
| Line item `catalog_object_id` / variation SKU | `OrderLine.sku` (match via existing SKU lookup) |
| Line item named e.g. "Delivery" | order-level delivery flag; not a product line |
| Order `fulfillments[]` type `PICKUP` / `SHIPMENT` | `fulfilment_method`; pickup `pickup_at`/`expires_at` -> `collect_by` (verify) |
| Order `customer_id` | not imported (customer stays in Square) |
| Order totals / tax | `grand_total`, `subtotal`, `tax_charged` |
| Tender / payment status `COMPLETED` | `PaymentState.settled` |
| Payment processing fee (Payments API) | `payment_fees` / `payment_net` |
| Order `state` `CANCELED` | `is_cancelled` (feeds existing pending-cancellation flow) |
| Order `updated_at` | `last_modified` (sync watermark) |

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

1. **Spike (sandbox):** Square developer account, sandbox seller, create a test order with a
   note, customer and pickup fulfilment; pull it with the API and inspect the real JSON.
   Confirms every "verify" above before any schema work.
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
- Square API field names/behaviour unverified until the spike.

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
