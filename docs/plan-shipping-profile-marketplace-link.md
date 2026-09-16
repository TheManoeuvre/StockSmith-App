# Plan: link StockSmith shipping profiles to Etsy / eBay and keep the buyer price in sync

**Status (2026-09-16):** all three stages implemented on branch `claude/stocksmith-shipping-sync-11664b`. Kept as the design record; the user-facing summary is the CHANGELOG entry.

Follow-up to the margin fix that made postage charged count as revenue
(`pricing.compute_profit_margin`). That fix makes `ShippingProfile.price` — what the buyer is
charged for postage — a number that directly moves every product's margin, so it has to be
right. Today it is typed in by hand and can silently drift from what the marketplace actually
charges. This ticket makes it a synced fact, makes the product's shipping profile the single
source of "how this ships" for draft listings too, and keeps the imported prices fresh
automatically.

The prompt below is self-contained for a fresh session. It is one piece of work delivered in
three stages on one branch; each stage should leave the app working and tested before the next
starts, and the CHANGELOG entry is written once, for the whole thing.

---

## Prompt

> **Context.** StockSmith's `ShippingProfile` (`backend/app/models/shipping_profile.py`) holds
> `price` (what the buyer is charged for postage — one value, channel-agnostic today) and
> per-channel seller costs `cost_etsy` / `cost_ebay` / `cost_manual`, resolved by
> `services/shipping_profiles.py::resolve_shipping_cost_for_fee_source`. Product margin
> (`services/pricing.py::compute_profit_margin`, mirrored in
> `frontend/src/components/products/PricingSection.tsx::computeMargin`) counts `price` as
> revenue, so it must match what Etsy/eBay actually charge the buyer.
>
> Separately, `ListingProfile` (`backend/app/models/listing_profile.py`) stores
> `etsy_shipping_profile_id` and `ebay_fulfillment_policy_id` — the *marketplace's* profile
> ids — which `services/draft_listing.py` maps into draft metadata
> (`"etsy.shipping_profile_id"`, `"ebay.fulfillment_policy_id"`) for `createDraftListing`, and
> which `services/draft_readiness.py` blocks a draft on when missing.
> `EtsyAdapter.fetch_shipping_profiles` (`services/platforms/etsy.py`) already fetches Etsy's
> profiles as `{id, title}` (needs the `shops_r` scope; 403 is reported as a reconnect
> blocker). Nothing fetches eBay fulfillment policies yet — the id is typed in by hand.
> `services/listing_profile_backfill.py` proposes listing profiles by grouping existing Etsy
> listings by a metadata signature that includes `shipping_profile_id`.
>
> Background work runs as one asyncio task per platform started from `app/main.py`'s
> lifespan — see `services/sync_scheduler.py` (order sync; per-platform lock shared with the
> manual "Sync now" endpoint, consecutive-auth-failure backoff, a per-tick timeout).
> Marketplace errors are `PlatformAuthError` / `PlatformRateLimitError` / `PlatformSyncError`
> from `services/platforms/errors.py`; user-facing failures go through
> `services/notification_alerts.py`, with alert types registered in `ALERT_TYPES` in
> `app/seed.py`.
>
> **Goal.** A StockSmith `ShippingProfile` can be linked to one Etsy shipping profile and/or
> one eBay fulfillment policy. The buyer-charged postage is imported from the marketplace per
> channel and refreshed automatically, with every change recorded and alerted. The product's
> shipping profile becomes the single source of "how this ships": draft listings take their
> marketplace profile id from it, and `ListingProfile`'s own shipping fields become a fallback.
>
> **Design decisions already made — do not relitigate:**
>
> 1. **Per-channel buyer price, mirroring the cost split.** Add nullable `price_etsy` and
>    `price_ebay` to `ShippingProfile`; keep `price` as the manual/default figure. Add
>    `resolve_shipping_price_for_fee_source(profile, fee_source)` next to the cost resolver
>    (etsy → `price_etsy ?? price`, ebay → `price_ebay ?? price`, manual → `price`) and use it
>    everywhere `profile.price` feeds margin or fee calculation: `services/pricing.py`,
>    `services/platform_fees.py::resolve_variant_fee_percent`, `routers/products.py`
>    (`effective_shipping_price`), and the frontend `computeMargin` / `CogsBreakdown` /
>    `ChannelFeeComparison` (which uses each channel's own price in its row). The same
>    physical service is genuinely charged at different prices per marketplace, exactly as it
>    costs different amounts per marketplace; a single field would force the refresh to
>    either overwrite or ignore one channel.
> 2. **Link columns on `ShippingProfile`:** nullable `etsy_shipping_profile_id: int` and
>    `ebay_fulfillment_policy_id: str`, unique per platform (one local profile per marketplace
>    profile). Archived profiles keep their link.
> 3. **Import takes the domestic destination.** Etsy `getShopShippingProfiles` returns
>    `shipping_profile_destinations[].primary_cost` per destination; use the one whose
>    `destination_country_iso` equals the profile's `origin_country_iso` (fall back to the
>    first destination and say so in the UI). Ignore `secondary_cost` (combined-shipping price
>    for additional items) and `shipping_profile_upgrades` — out of scope, note it in the
>    model docstring. For `profile_type == "calculated"` there is no fixed price: import
>    nothing and show "calculated on Etsy" in the picker. eBay:
>    `GET /sell/account/v1/fulfillment_policy?marketplace_id=…` →
>    `fulfillmentPolicies[].shippingOptions[]` with `optionType == "DOMESTIC"` → first
>    `shippingServices[]` (lowest `sortOrder`) → `shippingCost.value`; `costType ==
>    "CALCULATED"` imports nothing, same as Etsy. Use `ebay_marketplace_id` from the eBay
>    default `ListingProfile` when present, else `EBAY_GB`.
> 4. **Marketplace is the source of truth for the buyer price.** Import and refresh write
>    `price_<platform>` directly — never `price` or any `cost_*` (the marketplace knows nothing
>    about what the carrier charges the seller). Linking alone does not import; import is an
>    explicit action or the scheduled refresh.
> 5. **Refresh piggybacks on the existing per-platform sync tick — no third scheduler.** In
>    `sync_scheduler._tick` (or a hook at the end of `commit_sync`), after a successful order
>    sync, run `shipping_price_sync.refresh(platform)` at most once per
>    `shipping_price_refresh_hours` (new column on `PlatformConnection`, default 24, with a
>    `last_shipping_price_refresh_at` timestamp beside it). Postage prices change on the
>    order of weeks; one extra API call per platform per day is the right cost. One fetch per
>    platform, matched to linked local profiles in memory. Runs under the platform lock so it
>    never interleaves with a manual sync.
> 6. **Overwrite, record, and alert — don't ask.** A changed price is written straight to
>    `price_<platform>`. Every change is recorded in a new `shipping_profile_price_events`
>    table (profile id, platform, old, new, `changed_at`, `source: sync | manual_import |
>    user_edit`) so "why did margin drop on the 3rd" has an answer, and each refresh raises
>    one alert listing the profiles that moved (new alert type, digest delivery by default).
>    A linked profile that vanished upstream (Etsy `is_deleted`, or missing from the eBay
>    list) keeps its last price and raises an immediate-class alert naming the profile —
>    drafts pushed with that id will fail, and margin is running on an unverifiable number.
>    Do not clear the link automatically. Calculated marketplace profiles are skipped with no
>    alert.
> 7. **The product's `ShippingProfile` wins over `ListingProfile` for the draft's marketplace
>    profile id.** `ListingProfile.etsy_shipping_profile_id` / `ebay_fulfillment_policy_id`
>    stay as columns, relabelled as a fallback used only when the product has no linked
>    shipping profile.
> 8. **Do not build pushing local profiles to the marketplace.** Etsy cannot create calculated
>    profiles by API and the in-platform editors are better for the rare edit.
>
> ---
>
> **Stage 1 — Link and import.**
>
> - Alembic migration (SQLite-portable — follow the `batch_alter_table` pattern in existing
>   migrations) adding the five `ShippingProfile` columns; update
>   `backend/scripts/migrate_pg_to_sqlite.py` if it enumerates columns.
> - `EtsyAdapter.fetch_shipping_profiles`: extend the returned dict with `profile_type`,
>   `origin_country_iso`, and `domestic_price: Decimal | None` derived per decision 3. Keep the
>   `{id, title}` shape the existing Settings picker consumes.
> - New `EbayAdapter.fetch_fulfillment_policies(session, connection) -> list[dict]` with the
>   same shape (`id`, `title` = `name`, `domestic_price`), following the `_authed_request` /
>   error-mapping conventions in `services/platforms/ebay.py`. Router endpoint beside
>   `GET /platforms/etsy/shipping-profiles` in `routers/platforms.py`.
> - `routers/shipping_profiles.py` / `schemas/shipping_profile.py`: accept the link ids and
>   per-channel prices on create/update;
>   `POST /shipping-profiles/{id}/import-price/{platform}` fetches the linked marketplace
>   profile and writes `price_<platform>`. 404 when the link is missing, 409 when the
>   marketplace profile is calculated (message says so), and surface `PlatformSyncError` as
>   the reconnect-required blocker it already is.
> - `frontend/src/components/settings/ShippingProfileSettings.tsx`: two link pickers (Etsy
>   profile title / eBay policy name from the platform endpoints; disabled with a hint when
>   that platform is not connected), `price_etsy` / `price_ebay` money fields with `price` as
>   placeholder, a "Pull price from Etsy / eBay" action per channel, and a drift marker when a
>   stored per-channel price differs from what the last fetch returned.
> - Tests: adapter parsing (manual vs calculated, domestic pick, fallback); import endpoint
>   404/409/blocker paths; the price resolver per fee source; a `compute_profit_margin` case
>   proving a profile with `price_etsy` set uses it under the etsy fee source and `price`
>   under manual.
>
> **Stage 2 — Derive the listing-side profile from the product's shipping profile.**
>
> - `draft_listing.py`: when building metadata, resolve the product/variant shipping profile
>   (`services/shipping_profiles.py::resolve_variant_shipping_profile`) and, if it has the link
>   id for the target platform, use it in preference to the `ListingProfile` value. Record
>   which source won in the draft so the modal can say so.
> - `draft_readiness.py`: the "Shipping profile" / "Postage policy" blockers read "assign a
>   shipping profile that is linked to Etsy/eBay, or set one on the listing profile" and link
>   to the settings page for the case that applies. A product whose shipping profile is *not
>   linked* is a distinct message from a product with no shipping profile at all.
> - `listing_profile_backfill.py`: drop `shipping_profile_id` from `_SIGNATURE_FIELDS` so it
>   no longer shatters proposals; instead, for each distinct Etsy `shipping_profile_id` seen on
>   matched listings with no linked local `ShippingProfile`, propose creating/linking one
>   (title from Etsy, `price_etsy` from the domestic price). Show these as a separate
>   "Shipping profiles" group in
>   `frontend/src/components/settings/EtsyProfileProposalsPanel.tsx`.
> - Adoption (`docs/listing-adoption.md`, the Etsy/eBay adopt flows): when the adopted
>   listing's marketplace profile id matches a linked local `ShippingProfile`, set the
>   product's `shipping_profile_id` if it is null. Never overwrite a set one.
> - Product page Pricing tab: under the chosen shipping profile show "→ Etsy: <title>" /
>   "→ eBay: <name>" when linked, and an amber "not linked to Etsy" note when the product
>   targets that platform and the profile is not.
> - `frontend/src/components/settings/ListingProfiles.tsx`: relabel the two shipping fields
>   "Fallback shipping profile (used only when the product has none)".
> - Tests: draft metadata prefers the shipping-profile link and falls back to the listing
>   profile; readiness messages for linked / unlinked / none; backfill proposes a
>   shipping-profile link instead of splitting listing-profile proposals; adoption sets
>   `shipping_profile_id` only when null.
>
> **Stage 3 — Scheduled refresh.**
>
> - `services/shipping_price_sync.py` with `refresh(platform) -> RefreshResult` (changed,
>   unchanged, skipped_calculated, missing_upstream, error), the scheduler hook per decision
>   5, and the migration for the `PlatformConnection` columns and the
>   `shipping_profile_price_events` table.
> - `record_price_change(...)` is called by the refresh, by the Stage 1 import endpoint, and by
>   the Settings edit path, so the event log is complete rather than sync-only.
> - Auth failures: no second backoff counter — if the fetch raises `PlatformAuthError` the
>   existing sync loop's counter owns it (the order sync in the same tick failed the same
>   way). On `PlatformRateLimitError` skip this refresh and leave
>   `last_shipping_price_refresh_at` unset so the next tick retries.
> - Alerts per decision 6: register the two new alert types in `ALERT_TYPES`
>   (`app/seed.py`), with the "price changed" type digest by default and "linked profile
>   missing upstream" in `DEFAULT_IMMEDIATE_ALERT_TYPES`.
> - Manual trigger: `POST /shipping-profiles/refresh-prices/{platform}` runs the same routine
>   on demand; a "Refresh from Etsy / eBay" button on `ShippingProfileSettings.tsx` calls it
>   and shows `last_shipping_price_refresh_at` per platform with the count of profiles
>   changed. A collapsible "Price history" per profile reads the events table.
> - Tests: refresh writes only changed prices; records events with the right source; one
>   alert per refresh naming the changed profiles; deleted-upstream raises the immediate
>   alert and keeps the price; calculated skipped silently; rate-limit leaves the timestamp
>   unset; the tick honours `shipping_price_refresh_hours`; the manual endpoint works
>   without waiting for the tick.
>
> **Finish.** One CHANGELOG `[Unreleased]` entry written for the user, not the commit log,
> covering all three stages. Update the model docstrings on `ShippingProfile` and
> `ListingProfile` to describe the new relationship. Full backend suite, `tsc`, and vitest
> green before opening the PR.
