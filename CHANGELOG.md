# Changelog

All notable changes to StockSmith are recorded here.

The release workflow reads the section matching the tag being built and uses it as the
GitHub Release body, which in turn becomes the `notes` field in `latest.json` — that's
what the in-app update prompt shows. So whatever is written here is what users read when
deciding whether to install an update: write it for them, not for the commit log.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **StockSmith now tells you what needs counting.** Stock drifts from what the app thinks
  you have — a dropped print, a miscount, something used and never recorded — and until now
  the only way to find out was to check everything. You can now say how often each thing is
  worth counting, and the dashboard lists what's come round. Nothing is blocked or nagged
  about; it's a list to work from.
- **Set how often by group, not one item at a time.** Give a whole category a tier — resin
  every month, packaging every three — and everything in it follows. Anything unusual can be
  set on its own and overrides the group. Each item's page says which of the two it's
  currently getting, so it's clear where to go and change it.
- **Products can have a type.** Keyring, coaster, whatever you sell — products had no way of
  being grouped before, only a name and a SKU. Set one on the product page or manage the list
  under Settings > Reference data; the Products list gains a column and a filter for it.
  Types are also what lets products be scheduled for counting by group.
- Sensible counting intervals are set up already (30, 60 and 90 days by tier) so this is
  useful without configuring anything first. Change any of them under Settings > General.

This is the groundwork for stock takes proper — scoping a count, entering it in the app or
from a spreadsheet, and reviewing the differences before anything is adjusted — which is
coming next. Nothing here changes any stock quantity on its own.

## [0.6.3] - 2026-08-14

Fixes an update that could apply itself only halfway, and makes setting up listing profiles
possible without going and looking up id numbers by hand.

### Fixed
- **Updating no longer leaves you running half of the old version.** Windows can't replace
  a file that's in use, so if StockSmith was running when an update installed, the app
  updated but the part of it that does the actual work didn't — and the two carried on
  together looking almost fine. That's what made the Integrations page fail after updating
  to 0.6.2. StockSmith now notices and tells you to close it fully and run the installer
  again, rather than carrying on in a half-updated state. **If you're on 0.6.2 and the
  Integrations page is erroring, this is why — installing this version fixes it.**
- **The window closes when you click the X.** If StockSmith wanted to ask about unsaved
  work but couldn't show the question, the close was cancelled and nothing appeared, which
  left the X looking broken. It now closes rather than trapping you.

### Changed
- **Listing profiles are set up by name, not by id number.** Etsy identifies your category,
  shipping profile and return policy by numbers it never shows you anywhere in Etsy itself.
  Now you search for a category the way you would on Etsy, and pick your shipping profile
  and return policy from a list of your own. Return policies are described by what they do
  — "Returns and exchanges within 30 days" — because Etsy doesn't give them names.
- **Options read as words.** "I did" rather than `i_did`, and eBay's item condition is a
  list to choose from instead of something to type.
- **Etsy needs reconnecting once.** Reading your shipping profiles needs a permission
  StockSmith didn't previously ask for. Everything else works as before until you do.

## [0.6.2] - 2026-08-14

Groundwork for creating listings from StockSmith, plus the tool that makes it possible:
your Etsy listings already hold descriptions, prices and photos that StockSmith never
stored, and it can now read them back.

### Added
- **Fill in missing descriptions, prices and photos from Etsy.** Settings > Integrations >
  Check Etsy. It reads the listings your products are already linked to and offers what
  they hold: the description, the price of each variation, and the main photo. Tick what
  you want and it fills them in. It never overwrites anything you've already typed, so
  it's safe to run again later — the second time it simply finds less to do.
- **See which products won't fit Etsy's or eBay's rules before you try to list them.** The
  two stores cap things differently — a SKU can be 50 characters on eBay but only 32 on
  Etsy, a title 140 on Etsy and 80 on eBay — and StockSmith now checks your products
  against both and says which ones would be rejected, and by which store. It only appears
  when something needs attention.
- **Listing profiles.** Etsy and eBay both need details an inventory system never cared
  about: a category, who made the item, which postage policy applies. Most products answer
  these the same way, so a profile answers them once and products use it. StockSmith can
  suggest profiles by reading the listings you already have. On its own this doesn't do
  anything visible yet — it's the setup that lets StockSmith create a listing for you in a
  future update.
- **Separate listing titles from product names.** A product name is what you call it; a
  listing title is written for search and has a length limit. You can now keep both, and
  set a different one per store when Etsy's longer limit is worth using.
- **Correct a store's limits yourself.** If Etsy or eBay changes one — or one of
  StockSmith's built-in values turns out to be wrong — you can change it in Settings
  without waiting for an update.

### Fixed
- **Bulk-editing BOM overrides no longer quietly does nothing.** The "Value" box was free
  text, so a typo, a capital letter in the wrong place or a stray space matched no
  variants at all and the preview came back empty — which looked identical to there being
  nothing to change. It's now a list of the values actually in use.

## [0.6.1] - 2026-08-09

**Back up your data before updating.** This release changes the database and the app
applies that automatically on first launch. Nothing here is designed to lose data, but
this is the last update where you have to do that by hand — from now on StockSmith backs
itself up.

### Added
- **StockSmith now backs itself up.** Settings > Backup. One backup is a single zip
  holding your database and every product image. It runs automatically once a day while
  the app is open, keeps the last seven, and you can take one on demand at any time.
- **Copy each backup to a second folder.** Point it at OneDrive, Dropbox or anywhere that
  syncs, and your backups end up somewhere other than this computer — which is the copy
  that still helps if this computer is the thing that fails. If that folder ever stops
  working, StockSmith tells you instead of failing quietly.
- **Restore from a backup.** Choose one, confirm, and StockSmith restarts and puts your
  data back as it was. A snapshot of your current data is taken first, so a restore can
  itself be undone. Marketplace connections are deliberately not included in backups, so
  after restoring onto a different computer you'll need to reconnect Etsy and eBay —
  nothing else is lost. Restore runs on the computer hosting StockSmith; other devices
  wait and reconnect on their own.
- **Reference data can be edited.** Manufacturers, suppliers, material types and the new
  Colours list all live under Settings > Reference data, and open in place so you can
  correct several without leaving the page. Renaming one updates every material, purchase
  and order that uses it. Duplicates — the two spellings of one supplier that built up
  over time — can be merged into a single entry.
- **Colours are a real list now.** They used to be free text typed onto each material, so
  "Black", "black" and "BLACK" were three different colours. Existing values are merged
  automatically on first launch, keeping whichever spelling you used most.
- **Shipping profiles can be archived.** A profile you no longer offer disappears from the
  pickers while every past order it shipped under keeps its costs.
- **Unsaved changes are protected in Settings too.** Editing forecasting thresholds,
  marketplace credentials or backup settings and then navigating away now asks first,
  the same way the product and material pages already did.

### Changed
- **Products show one sellable figure instead of four raw numbers.** The list had three
  columns of buildable/sellable counts and the product page repeated them. There is now a
  single headline — what you can actually sell right now, which is also what's pushed to
  your marketplaces — with the breakdown in words underneath: how many are built and free,
  how many more you could build, and what open purchase orders would add.
- **Settings is reorganised.** General comes first and Connection last. Each marketplace's
  fee components moved onto that marketplace's own card under Integrations, and shipping
  profiles moved to Reference data. The Pricing tab keeps the one setting that really is
  shop-wide — which channel your margins are estimated for — and now shows which fees that
  choice actually applies.
- Copy buttons on product names and SKUs.

### Fixed
- **A quantity limit was reported as "nothing built" even when units were on the shelf.**
  The label only appears now when something genuinely external — packaging, or a platform
  quantity cap — is holding the figure down.
- **Deleting a shipping profile silently changed past orders.** It removed the profile from
  every product, variant and completed order that referenced it, quietly altering what those
  orders recorded being shipped under. Profiles in use can no longer be deleted; archive
  them instead.
- **StockSmith stopped writing its own log file after startup.** Applying database updates
  switched off the logging configured moments earlier, so `backend.log` was missing almost
  everything that happened after launch — which is exactly what's needed when diagnosing a
  problem.
- Reference data lists could never be corrected: there was no way to rename, merge or remove
  an entry once created, so a typo introduced by an import was permanent.

## [0.6.0] - 2026-08-06

**Back up your data before updating.** This release changes the database, the app
applies that automatically on first launch, and it cannot be undone.

**Two figures will move, on existing orders as well as new ones.** Net profit goes
**up** on any order with more than one item — packaging was charged per unit when only
one box was ever used. Product and variant margins read slightly **lower** — packaging
is now counted against them. Your stock, prices and orders are untouched; only the
arithmetic changed.

Orders shipped before this update need a one-off repair before their packaging cost
shows up — run `scripts/backfill_order_kitting_ledger.py` (dry run first, then
`--apply`). It moves no stock.

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
