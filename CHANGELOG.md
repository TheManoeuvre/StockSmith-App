# Changelog

All notable changes to StockSmith are recorded here.

The release workflow reads the section matching the tag being built and uses it as the
GitHub Release body, which in turn becomes the `notes` field in `latest.json` — that's
what the in-app update prompt shows. So whatever is written here is what users read when
deciding whether to install an update: write it for them, not for the commit log.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-08-06

### Added
- **Fee reporting signature** for eBay, in Settings > Integrations. eBay requires UK and
  EU sellers to digitally sign requests for financial data; setting this up once is what
  lets StockSmith read your eBay fees. See
  [docs/ebay-fee-reporting.md](docs/ebay-fee-reporting.md).
- **Bill of Materials and Kitting BOM are now one tab**, with the two tables stacked and their
  columns lined up so you can read a product's build cost and packaging cost together.
- **Both tables now show cost.** Each line shows what its quantity costs at the material's
  current price and what share of the total that is, with a total under each table — so it's
  obvious at a glance which material is driving a product's cost.
- **Save buttons stay greyed out until there's something to save**, everywhere on the product
  page. Previously every Save button was always clickable, so it never told you anything.
- **You're now warned before losing unsaved edits** — switching tabs, collapsing a variant,
  changing the pricing mode, leaving the page, or closing the app window all ask first, and
  name what's unsaved. This covers the Materials pages too: editing a material's details, a
  half-typed stock adjustment, or a part-filled new-material form.
- **Save buttons on the Materials pages** follow the same rule as products — greyed until
  there's something to save, and stock adjustments stay disabled until they have both a value
  and a reason.

### Fixed
- **eBay platform fees were never imported.** Every eBay order showed its fees as "Not
  yet settled" indefinitely, even after eBay had taken them and made the payout
  available — because eBay was rejecting StockSmith's request for the fee breakdown and
  the failure was never reported anywhere. Net profit on every eBay order was overstated
  by the missing fee as a result. Fixing this needs a one-off setup step: Settings >
  Integrations > eBay > **Fee reporting signature**. Orders already imported keep their
  blank fees until backfilled — see
  [docs/ebay-fee-reporting.md](docs/ebay-fee-reporting.md).
- eBay fee data was also being requested from the wrong address (`api.ebay.com` rather
  than `apiz.ebay.com`), and the amount eBay pays out per order was being reduced by the
  fee a second time. Both were masked by the rejection above and are fixed together.
- An order with no fee figure now reads "Not reported yet" rather than "Not yet settled",
  which claimed to know something the app had no way of knowing.
- **Editing a product could silently discard your changes.** Saving anything on a product page
  refreshed the whole product, which wiped out unsaved edits in every other section — so typing
  a BOM quantity while a background refresh landed could lose it with no warning. Every editor
  on the page now keeps your edits until you save or discard them yourself.
- **A variant's name could show out of date.** Renaming a variant elsewhere never reached an
  open variant row, which kept displaying the old name until the page was reloaded.
- **Packaging was over-charged on every multi-unit order.** StockSmith has always known
  that an order shipping several units needs one box, not one per unit — the Kitting
  section showed exactly that — but cost of goods charged for a box per unit anyway. A
  three-unit order using a £1 box was charged £3 for packaging, so net profit was
  understated on every order with more than one item. Packaging cost is now taken from
  what the order actually consumed, and it moves when you change an override.
- **Cancelling a shipped order put back too much packaging.** The same per-unit assumption
  ran on returns, so cancelling a three-unit order returned three boxes to stock when only
  one had been used — and an order with two items returned the shared box twice.

### Changed
- **"Max theoretical" is now "Max from free stock"** in both BOM tables, and both count only
  material that isn't already reserved against an order. The build BOM previously counted all
  stock on hand, which read higher than what you could actually build today.
- **Cost of goods is now split into Materials and Kitting** on the order page, instead of
  one combined figure. A line's Cost is the materials to make it; packaging is shown once
  for the order, because that is how it is bought and used. The order's Kitting section
  now shows what each material costs and what the whole order's packaging comes to.
- **Product and variant margins now include packaging**, so they agree with the net profit
  shown on orders. Margins will read slightly lower than before — the packaging was always
  being paid for, it just wasn't counted here.
- **"Packaging" on the order page is now called "Kitting"**, matching the name used
  everywhere else.

### Notes
- Packaging cost is now frozen when an order ships, so a past order's profit no longer
  drifts as you re-buy boxes at new prices. Orders shipped before this update are valued
  at today's material cost, since their historical cost isn't recoverable.

## [0.5.0] - 2026-08-04

### Added
- Products list now has a **Stores** column showing a colour-coded badge per connected
  marketplace, so it's glanceable which stores each product is listed on.
- The menu bar now shows how long ago your stores last synced, with an alert badge when
  a sync failed or when stock updates aren't reaching a marketplace.
- Testing a store sync now flags any listing whose quantity has drifted from StockSmith's,
  with a **Push corrections** button to set the marketplace back to StockSmith's numbers.
  Testing itself stays read-only — nothing is pushed until you click.
- **Bulk-edit BOM overrides** on the Variants tab corrects a BOM line across every variant
  sharing an attribute value (e.g. every "Large"), instead of editing each one by hand. It
  previews exactly what would change before anything is written.

### Fixed
- Generating variants with conflicting BOM rules now explains the conflict and names the
  attribute value responsible, instead of failing with "Internal server error".
- Editing a variant's BOM now pushes the new stock figure to your marketplaces. Previously
  the corrected number stayed local until some other stock change happened to trigger a push.
- The **eBay variation** column on the Platform Sync tab is no longer always blank — it now
  shows each SKU's variation (e.g. "Model: Button Dual") in the same format as Etsy's.
- eBay listing status is now read from the actual listing rather than assumed active, so a
  listing that's ended, inactive or not yet published is reported as such. A sold-out
  listing still counts as active, since eBay keeps it live at quantity 0.
- Transient eBay server errors (`errorId 25001`, "Dependent service failure") are now
  retried automatically instead of failing immediately — this most often showed up when
  migrating a listing with **Migrate & link**.
- Products whose eBay listing has no title no longer render a blank cell on the Platform
  Sync tab; they show the same "—" placeholder as any other missing value.
- Platform Sync tables for Etsy and eBay now line up column-for-column regardless of how
  long either listing title is.

## [0.4.2] - 2026-08-03

### Fixed
- Migrating a multi-variation eBay listing no longer times out partway through.

## [0.4.1] - 2026-08-02

### Fixed
- The eBay reconnect banner could get stuck on screen with no way to clear it.

## [0.4.0] - 2026-08-01

### Added
- Listing adoption: link existing eBay listings that were never migrated to the Inventory
  API, and Etsy listings that aren't linked to a StockSmith product yet.

## [0.3.5] - 2026-07-31

### Added
- Materials now show a Weeks-of-Supply forecast in place of the old static reorder
  threshold.

## [0.3.4] - 2026-07-30

### Changed
- The auto-created shipping label was replaced with a configurable default kitting BOM.

## [0.3.3] - 2026-07-29

### Added
- Builds, adjustments and order fulfilment are now unified into a single Stock history.

### Changed
- Order-line COGS is snapshotted at first allocation and costed by shipped quantity.
- New products automatically get the default shipping label on their kitting BOM.
- Packaging kitting defaults to qty 1 for multi-unit orders.

## [0.3.2] - 2026-07-28

### Fixed
- Marketplace orders are only imported once payment has settled.
- SQLite foreign-key enforcement is on, so deleting an order cascades correctly.

## [0.3.1] - 2026-07-27

### Added
- Delete-order and deactivate-product buttons.

### Fixed
- Dashboard boot race and Orders connection-pool exhaustion.
- Orders and Products lists are paginated, with an index on order placement date.
