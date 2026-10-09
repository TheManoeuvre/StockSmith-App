# StockSmith — Per-Product Store Toggles: Planning Pass

## Status

Implemented on branch `claude/per-product-store-toggles-4f0ba8` (migration `a7c2e4b19d35`).
Built as recommended in "Decisions needed", with these departures from the plan below:

- **New products**: only an *off* default writes a row (`is_target = false`). An on default
  writes nothing — unset already means on, and changing the store default later never moves
  an existing product either way, so explicit "on" rows would buy nothing.
- **CSV**: import applies the store defaults to created products; export has no per-store
  column yet.
- **Products list**: off stores lose their badge; the "Sold on" filter is not built (the
  list is server-paginated, so it needs a backend query param).
- **Notification alert scoping** (`check_shipping_profile_missing_alerts`) and the
  new-product form's store chips are not built.
- **Different-account reconnect** clears `product_scope_decided_at`, so the prompt is asked
  again for a different shop.
- The functional UI uses existing components; the visual pass is still the Claude Design
  hand-off.

Original planning findings
 below come from reading
`services/product_platforms.py`, `models/listing_profile.py` (`ProductPlatformSettings`),
`models/platform_connection.py`, and the product/store UI as they exist today.

## Goal

Some products are only ever listed on one marketplace, not all of them. Right now every
product carries full UI, warnings and API traffic for every connected store regardless of
whether it's actually sold there. Add a per-product, per-store on/off switch so:

- a product's page only shows the stores it's actually sold on
- warnings/validation for a store (e.g. "too many variations for eBay") don't fire for
  products that were never going there
- background jobs (sync checks, compatibility scans) skip products that are off for that
  store, which also reduces marketplace API usage
- a store's settings can declare a default for new products ("on" or "off"), and the very
  first time that default is used, the user is prompted to bulk-apply it across existing
  products — once, not on every reconnect

## What already exists (and helps)

- `ProductPlatformSettings.is_target` (`models/listing_profile.py:119`) is a nullable
  per-product-per-platform boolean that already means roughly "is this product aimed at
  this platform". It's written by the product settings `PUT`
  (`routers/platforms.py:1177`) but nothing in the UI sets it today — the docstring on the
  model says as much: "written now and read later."
- `services/product_platforms.target_platforms` already resolves this flag: null falls
  back to "connected + live", an explicit `False` excludes the platform, and a platform
  the product is genuinely live on can never be excluded (`services/product_platforms.py:57`).
  That asymmetry — live always wins — is exactly the "locked on" behaviour this feature
  needs, so no new rule is required there.
- `services/variant_platform_conflicts.py` already reads `target_platforms`, so the eBay
  variation-count warning stops firing for an eBay-excluded product for free once the flag
  is actually set anywhere.
- `PlatformConnection` (`models/platform_connection.py`) is the natural home for a
  per-store default and a "have we asked yet" marker — it's already one row per platform
  and already survives disconnect/reconnect of the same shop.

## What's missing / must change

### Data model

1. **`PlatformConnection` — two new columns:**
   - `new_products_default_on: bool`, default `True`. Whether a newly created product
     starts on for this store.
   - `product_scope_decided_at: datetime | None`. Set the first time the user answers the
     "turn all products on or off" prompt for this store. `None` means never asked.
   Both survive disconnect/reconnect of the same shop, same as `sync_start_date` and the
   other fields `disconnect_platform` deliberately leaves alone
   (`routers/platforms.py:707`).
2. **`ProductPlatformSettings.is_target` stays the per-product switch.** No new column
   needed there — it's already exactly the right shape (nullable bool, unique on
   product+platform).
3. **Migration:** add the two `PlatformConnection` columns. Backfill
   `product_scope_decided_at` to `NULL` for existing rows (Etsy/eBay are already connected
   and unset, so the first store-settings visit after this ships prompts once, as
   intended — see "Decisions" below).

### Resolver (`services/product_platforms.py`)

4. Add a function that answers "is product X on for platform Y", combining:
   - live listing → always on (existing rule, unchanged)
   - explicit `is_target` → use it
   - unset and store's scope has been decided → treat unset as the *opposite* of nothing,
     i.e. still fall through to today's derived behaviour (connected ⇒ on) — an unset row
     after the prompt just means "this specific product hasn't been touched since", which
     stays on unless someone flips it
   - unset and store's scope has never been decided → on (matches current behaviour
     exactly, so nothing changes until the store owner opts in)
5. Add a bulk variant for list/grid screens (one query for N products) so the products
   list and compatibility panel don't do N+1 lookups.
6. `catalogue_target_platforms` (whole-catalogue scan) needs to honour per-product
   exclusion the same way `target_platforms` does, or the catalogue compatibility scan
   will keep reporting on products that are deliberately off.

### Downstream consumers

7. `services/catalogue_compatibility.py` — skip excluded products from the scan; report an
   "N products excluded from this store" count so it's not just silently smaller.
8. `services/listing_sync.py` (`check_all_products_sku_sync`,
   `get_all_stored_sync_status`) — skip products that are off for that platform. This is
   the actual API-budget win: fewer eBay/Etsy calls against the 5,000/day budget
   (`project_ebay_existing_store_onboarding_parked` memory).
9. `services/listing_reconcile.py`, `services/listing_push.py` — no filtering expected;
   both only ever act on rows that already have a `Listing`, and a live listing is always
   on by the existing rule. Worth a read-through to confirm there's no path that creates a
   `Listing` for a product that's off, before relying on that invariant.
10. `services/draft_listing.py`, `services/listing_adoption.py`,
    `services/etsy_backfill.py` — creating a draft or adopting/matching an existing
    listing is a clear "this product is sold here" signal. These should set
    `is_target = True` explicitly (not rely on live-listing-always-wins), so the product
    stays on for that store even after the listing later ends and the "live" override no
    longer applies.
11. `services/notification_alerts.check_shipping_profile_missing_alerts` — optionally
    scope to shipping profiles actually used by on-for-that-store products, so a profile
    only relevant to eBay doesn't alert on Etsy's panel. Lower priority; only worth doing
    if it's cheap once the resolver exists.
12. `routers/products.py` `create_product` / CSV import
    (`services/csv_io.import_products_csv`) — apply each connected store's
    `new_products_default_on` when a product is created (row insert into
    `ProductPlatformSettings` per connected platform, or leave unset and let the resolver
    default — needs a decision, see below). CSV export should include an on/off column per
    connected store so round-tripping doesn't lose the setting.
13. Order sync — deliberately **not** filtered. An order that arrives for a product marked
    off for that store still needs to be imported and fulfilled; the toggle governs
    listing/warning/scan behaviour, not order intake.

### API

14. Product read/list responses: include each connected store's on/off state and whether
    it's locked (has a live listing there), alongside the existing `live_platforms`
    (`schemas/product.py:288`).
15. Bulk endpoint: "set all products' `is_target` for platform P to true/false", which also
    stamps `product_scope_decided_at`. This is what the first-time prompt calls.
16. Endpoint (or fold into the existing platform credentials/status `PUT`) to change
    `new_products_default_on`.
17. The existing per-product settings `PUT` (`routers/platforms.py:1160`) already accepts
    `is_target` — no change needed there, just a UI that finally calls it.
18. Backup/restore (`services/backup.py`, `services/restore.py`) — confirm the two new
    `PlatformConnection` columns and existing `is_target` are already covered by whatever
    generic column enumeration those use; add explicitly if not.

### Frontend

19. **Product page Stores tab** (`routes/products/$productId.tsx:851`,
    `ProductStoresSettings.tsx`, `ProductPlatformSettingsPanel.tsx`,
    `PlatformSyncSection.tsx`) — currently hard-codes `["etsy", "ebay"]` and renders both
    panels in full always. Change to:
    - full panel for a store the product is on
    - a single collapsed row ("Off for eBay — [Turn on]") for a store the product is off
      for, disabled/greyed if locked on by a live listing (can't turn off, with a link to
      end the listing first)
    - disconnected stores hidden entirely (already effectively true via
      `CONNECTABLE_PLATFORMS` patterns elsewhere)
20. Product header/tabs — small on/off chips per store, visible from any tab, so the state
    is discoverable without opening Stores.
21. **Products list** (`routes/products/route.tsx:526`, `PlatformSyncBadge`) — dim or hide
    a store's badge when the product is off for it; add a "Sold on" filter.
22. **`PlatformConflictDialog`** (the 409 flow in `variant_platform_conflicts.py` /
    `variants.py:448`) — add a "Don't sell this on eBay" action alongside "Proceed
    anyway", which calls the per-product toggle directly. This is the most natural
    discovery point for the whole feature, since it's the moment the friction is felt.
23. **`PlatformCompatibilityPanel`** — surface the "N products excluded" count from #7.
24. **Store settings page** (`components/settings/stores/StorePage.tsx`) — new card:
    - "Turn on new products by default" toggle (`new_products_default_on`)
    - on/off counts for existing products
    - manual "apply to all products" action (same bulk endpoint as the prompt, so the
      prompt isn't the only way to reach it)
25. **First-time prompt dialog** — shown once per store, the first time its settings page
    is opened after this ships (or the default toggle is touched — see "Decisions").
    "Turn all products on for eBay, or off?" with a note that products already live on
    eBay stay on either way. Answering stamps `product_scope_decided_at` so it never
    reappears, including across disconnect/reconnect of the same shop.
26. **New-product form** — store chips pre-filled from each connected store's current
    default, editable before save.

## Decisions needed

1. **Switching off a product that's live on that store.** Recommend: keep it locked on
   (can't uncheck) with a "end the listing on eBay first" hint, matching the existing
   live-always-wins rule in `target_platforms`. Alternative (stop pushing stock while
   leaving the listing live) risks overselling and isn't recommended.
2. **When the first-time prompt fires.** Two readings of "first time enabled": (a) the
   first time the store is *connected*, or (b) the first time its settings page is opened
   after this ships. Etsy and eBay are already connected today, so (a) has already
   happened for existing users. Recommend (b): prompt on first visit to each store's
   settings page post-upgrade, since that's also where the default toggle lives.
3. **Square.** No listings to push — StorePage already special-cases it as listings-free
   (`StorePage.tsx:169`). Recommend leaving Square out of this feature entirely.
4. **Reconnecting a different shop under the same platform slot.** The user asked that
   disconnect/reconnect not re-prompt. Confirm that's still wanted even when the OAuth
   callback detects a *different* account id (`routers/platforms.py` account-id-changed
   path) — a different shop plausibly has a different product mix, so re-prompting there
   might be the more correct behaviour. Recommend re-prompting only in the
   different-account case, since that's the one place the app can actually tell.
5. **New product default write timing.** Should `new_products_default_on` be applied by
   writing an explicit `ProductPlatformSettings` row at creation time, or left unset and
   handled purely by the resolver reading the store's current default at read time? The
   former is auditable and survives the store default changing later without silently
   moving already-created products; the latter is simpler but means changing the store
   default retroactively changes products that were "on by default" and never explicitly
   set. Recommend the former (write explicit rows), matching #10's reasoning for drafts.

## Suggested build order

1. Migration + `PlatformConnection` columns.
2. Resolver changes in `product_platforms.py` (single-product + bulk).
3. Wire `variant_platform_conflicts`, `catalogue_compatibility`, `listing_sync` scans to
   the resolver — this alone delivers the "fewer warnings, fewer API calls" half of the
   goal even before any new UI exists.
4. API: read/list exposure, bulk toggle endpoint, store default endpoint.
5. Hand the components listed under "For Claude Design" (see chat) to design; build UI
   once designs are back.
6. Product page Stores tab, header chips, products list filter, conflict dialog action,
   store settings card, first-time prompt, new-product form defaults.
