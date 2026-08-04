# Changelog

All notable changes to StockSmith are recorded here.

The release workflow reads the section matching the tag being built and uses it as the
GitHub Release body, which in turn becomes the `notes` field in `latest.json` — that's
what the in-app update prompt shows. So whatever is written here is what users read when
deciding whether to install an update: write it for them, not for the commit log.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
