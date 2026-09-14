# Plan: link StockSmith shipping profiles to Etsy / eBay shipping profiles

Follow-ups to the margin fix that made postage charged count as revenue
(`pricing.compute_profit_margin`). That fix makes `ShippingProfile.price` — what the buyer is
charged for postage — a number that now directly moves every product's margin, so it has to be
right. Today it is typed in by hand and can silently drift from what the marketplace actually
charges. These three prompts, in order, make it a synced fact.

Each section is a self-contained prompt for a fresh session. Run them in order; 2 and 3
assume 1 has merged.

---

## Prompt 1 — Link shipping profiles to their marketplace counterparts and import the buyer price

> **Context.** StockSmith's `ShippingProfile` (`backend/app/models/shipping_profile.py`) holds
> `price` (what the buyer is charged for postage — one value, channel-agnostic today) and
> per-channel seller costs `cost_etsy` / `cost_ebay` / `cost_manual`, resolved by
> `services/shipping_profiles.py::resolve_shipping_cost_for_fee_source`. Product margin
> (`services/pricing.py::compute_profit_margin`, mirrored in
> `frontend/src/components/products/PricingSection.tsx::computeMargin`) now counts `price` as
> revenue, so it must match what Etsy/eBay actually charge the buyer.
>
> Separately, `ListingProfile` (`backend/app/models/listing_profile.py`) stores
> `etsy_shipping_profile_id` and `ebay_fulfillment_policy_id` — the *marketplace's* profile
> ids, used only when pushing a draft listing. `EtsyAdapter.fetch_shipping_profiles`
> (`services/platforms/etsy.py`) already fetches Etsy's profiles as `{id, title}` (needs the
> `shops_r` scope; 403 is reported as a reconnect blocker). Nothing fetches eBay fulfillment
> policies yet — the id is typed in by hand.
>
> **Goal.** Let a StockSmith `ShippingProfile` be linked to one Etsy shipping profile and/or
> one eBay fulfillment policy, and import the buyer-charged postage from the marketplace so
> `price` stops being a hand-typed guess.
>
> **Design decisions already made:**
> 1. **Per-channel buyer price, mirroring the cost split.** Add nullable `price_etsy` and
>    `price_ebay` to `ShippingProfile`; keep `price` as the manual/default figure. Add
>    `resolve_shipping_price_for_fee_source(profile, fee_source)` next to the cost resolver
>    (etsy → `price_etsy ?? price`, ebay → `price_ebay ?? price`, manual → `price`) and use it
>    everywhere `profile.price` feeds margin or fee calculation: `services/pricing.py`,
>    `services/platform_fees.py::resolve_variant_fee_percent`, `routers/products.py`
>    (`effective_shipping_price`), and the frontend `computeMargin` / `CogsBreakdown` /
>    `ChannelFeeComparison` (which should use each channel's own price in its row).
>    Rationale: the same physical service is genuinely charged at different prices per
>    marketplace, exactly as it costs different amounts per marketplace, and a single field
>    would force the sync in prompt 3 to either overwrite or ignore one channel.
> 2. **Link columns on `ShippingProfile`:** nullable `etsy_shipping_profile_id: int` and
>    `ebay_fulfillment_policy_id: str`. Unique per platform (one local profile per
>    marketplace profile). Archived profiles keep their link.
> 3. **Import takes the domestic destination.** Etsy `getShopShippingProfiles` returns
>    `shipping_profile_destinations[].primary_cost` per destination country/region; use the one
>    whose `destination_country_iso` equals the profile's `origin_country_iso` (fall back to
>    the first destination and say so in the UI). Ignore `secondary_cost` (combined-shipping
>    price for additional items) and `shipping_profile_upgrades` — out of scope, note it in
>    the model docstring. For `profile_type == "calculated"` there is no fixed price: import
>    nothing and show "calculated on Etsy" in the picker. eBay: `GET
>    /sell/account/v1/fulfillment_policy?marketplace_id=EBAY_GB` →
>    `fulfillmentPolicies[].shippingOptions[]` with `optionType == "DOMESTIC"` → first
>    `shippingServices[]` (lowest `sortOrder`) → `shippingCost.value`; `costType ==
>    "CALCULATED"` imports nothing, same as Etsy. Reuse `ebay_marketplace_id` from the eBay
>    `ListingProfile` default when present, else `EBAY_GB`.
> 4. **Import is an explicit action, not implicit on link.** Linking sets the id; a "Pull
>    price from Etsy / eBay" button (or an "import on link" checkbox defaulting on) writes
>    `price_etsy` / `price_ebay`. Never touch `price` or any `cost_*` — the marketplace
>    knows nothing about what the carrier charges the seller.
>
> **Work:**
> - Alembic migration (SQLite-portable — see existing migrations for the `batch_alter_table`
>   pattern) adding the five columns; update `backend/scripts/migrate_pg_to_sqlite.py` if it
>   enumerates columns.
> - `EtsyAdapter.fetch_shipping_profiles`: extend the returned dict with `profile_type`,
>   `origin_country_iso`, and a `domestic_price: Decimal | None` derived as above. Keep the
>   `{id, title}` shape the existing Settings picker consumes.
> - New `EbayAdapter.fetch_fulfillment_policies(session, connection) -> list[dict]` with the
>   same shape (`id`, `title` (= `name`), `domestic_price`). Follow the existing `_authed_request`
>   / error-mapping conventions in `services/platforms/ebay.py`. Add the router endpoint
>   beside `GET /platforms/etsy/shipping-profiles` in `routers/platforms.py`.
> - `routers/shipping_profiles.py` / `schemas/shipping_profile.py`: accept the link ids and
>   per-channel prices on create/update; add `POST /shipping-profiles/{id}/import-price/{platform}`
>   that fetches the linked marketplace profile and writes `price_<platform>`. 404 the link
>   is missing, 409 if the marketplace profile is calculated (message says so), and surface
>   `PlatformSyncError` as the reconnect-required blocker it already is.
> - `frontend/src/components/settings/ShippingProfileSettings.tsx`: two link pickers (Etsy
>   profile title / eBay policy name, loaded from the platform endpoints; disabled with a
>   hint when that platform is not connected), the `price_etsy` / `price_ebay` money fields
>   with `price` as their placeholder, and the pull-price action per channel. Show a drift
>   marker when a stored per-channel price differs from what the last fetch returned.
> - `ListingProfile.etsy_shipping_profile_id` / `ebay_fulfillment_policy_id` stay as they are
>   in this prompt (prompt 2 derives them).
> - Tests: adapter parsing (manual vs calculated, domestic pick, fallback), the import
>   endpoint's 404/409/blocker paths, the price resolver per fee source, and a
>   `compute_profit_margin` case proving a product whose profile has `price_etsy` set uses
>   it under the etsy fee source and `price` under manual.
> - CHANGELOG `[Unreleased]` entry written for the user, not the commit log.
>
> Do not build pushing local profiles *to* the marketplace — Etsy cannot create calculated
> profiles by API and the in-platform editors are better for the rare edit.

---

## Prompt 2 — Derive the listing-side shipping profile from the product's shipping profile

> **Context.** After prompt 1, `ShippingProfile` carries `etsy_shipping_profile_id` and
> `ebay_fulfillment_policy_id`. `ListingProfile` (`backend/app/models/listing_profile.py`)
> carries the same two ids independently, and `services/draft_listing.py` maps them into
> draft metadata (`"etsy.shipping_profile_id"`, `"ebay.fulfillment_policy_id"`) which
> `EtsyAdapter` / `EbayAdapter` send on `createDraftListing`. `services/draft_readiness.py`
> blocks a draft when they are missing. `services/listing_profile_backfill.py` proposes
> listing profiles by grouping existing Etsy listings by a metadata signature that includes
> `shipping_profile_id`.
>
> **Problem.** A product can be priced under "Small Parcel 48" (its `ShippingProfile`) but
> listed under whatever its `ListingProfile` says — two fields that mean "how does this
> ship" and nothing keeps them agreeing. The margin estimate and the live listing can
> disagree about postage without anyone noticing.
>
> **Goal.** The product's resolved `ShippingProfile` is the single source of "how this
> ships"; the marketplace profile id on the draft comes from it. `ListingProfile` keeps its
> shipping fields only as a fallback for products with no shipping profile.
>
> **Work:**
> - `draft_listing.py`: when building metadata, resolve the product/variant shipping profile
>   (`services/shipping_profiles.py::resolve_variant_shipping_profile`) and, if it has the
>   link id for the target platform, use it in preference to the `ListingProfile` value.
>   Record which source won in the draft so the modal can say so.
> - `draft_readiness.py`: the "Shipping profile" / "Postage policy" blockers now read
>   "assign a shipping profile that is linked to Etsy/eBay, or set one on the listing
>   profile" and link to the right settings page for the case that applies. A product with
>   a shipping profile that is *not* linked should be a distinct message from a product with
>   no shipping profile at all.
> - `listing_profile_backfill.py`: drop `shipping_profile_id` from `_SIGNATURE_FIELDS` so it no
>   longer shatters proposals; instead, for each distinct Etsy `shipping_profile_id` seen on
>   matched listings that has no linked local `ShippingProfile`, propose creating/linking
>   one (title from Etsy, `price_etsy` from the domestic price per prompt 1). Show these as a
>   separate "Shipping profiles" proposal group in
>   `frontend/src/components/settings/EtsyProfileProposalsPanel.tsx`.
> - Adoption (`docs/listing-adoption.md`, the Etsy/eBay adopt flows): when adopting an
>   existing listing whose marketplace profile id matches a linked local `ShippingProfile`,
>   set the product's `shipping_profile_id` if it is null. Never overwrite a set one.
> - Product page: the Pricing tab's shipping-profile select shows a small "→ Etsy: <title>"
>   / "→ eBay: <name>" line under the chosen profile when linked, and an amber "not linked
>   to Etsy" note when the product targets that platform and the profile is not.
> - `ListingProfile.etsy_shipping_profile_id` / `ebay_fulfillment_policy_id`: keep the columns,
>   relabel in `frontend/src/components/settings/ListingProfiles.tsx` as "Fallback shipping
>   profile (used only when the product has none)".
> - Tests: draft metadata prefers the shipping-profile link; falls back to listing profile;
>   readiness messages for linked / unlinked / none; backfill proposes a shipping-profile
>   link instead of splitting listing-profile proposals; adoption sets `shipping_profile_id`
>   only when null.
> - CHANGELOG `[Unreleased]` entry.

---

## Prompt 3 — Scheduled refresh of marketplace shipping prices

> **Context.** After prompt 1, each `ShippingProfile` linked to an Etsy shipping profile or
> eBay fulfillment policy carries an imported `price_etsy` / `price_ebay`. Those are the
> revenue side of every product's margin, and they change whenever the shop edits postage
> in Etsy Shop Manager or eBay Business Policies — which StockSmith never sees.
>
> Background work in this app runs as one asyncio task per platform started from
> `app/main.py`'s lifespan — see `services/sync_scheduler.py` (order sync; per-platform
> lock shared with the manual "Sync now" endpoint, consecutive-auth-failure backoff, a
> per-tick timeout) and `services/backup_scheduler.py` / `services/notification_scheduler.py`
> for the smaller time-of-day pattern. Marketplace errors are `PlatformAuthError` /
> `PlatformRateLimitError` / `PlatformSyncError` from `services/platforms/errors.py`, and
> user-facing failures go through `services/notification_alerts.py`.
>
> **Goal.** Keep `price_etsy` / `price_ebay` in step with the marketplace automatically,
> and tell the user when a price moved, because a moved price moves margins.
>
> **Design decisions already made:**
> 1. **Piggyback on the existing per-platform sync tick, don't add a third scheduler.** In
>    `sync_scheduler._tick` (or a hook `commit_sync` calls at the end), after a successful
>    order sync, run `shipping_price_sync.refresh(platform)` at most once per
>    `shipping_price_refresh_hours` (new column on `PlatformConnection`, default 24; a
>    `last_shipping_price_refresh_at` timestamp beside it). Postage prices change on the
>    order of weeks; one extra API call per platform per day is the right cost. Reuse the
>    platform lock so it never interleaves with a manual sync.
> 2. **One fetch per platform, not one per profile.** `fetch_shipping_profiles` /
>    `fetch_fulfillment_policies` return the whole list; match linked local profiles by id in
>    memory.
> 3. **Overwrite, record, and alert — don't ask.** A changed price is written straight to
>    `price_<platform>`, because the marketplace is the source of truth for what the buyer
>    pays and a stale local number is simply wrong. Every change is recorded (new
>    `shipping_profile_price_events` table: profile id, platform, old, new, `changed_at`,
>    `source: sync | manual_import | user_edit`) so "why did margin drop on the 3rd" has an
>    answer, and raises one alert per refresh listing the profiles that moved (new alert
>    type in `notification_alerts.py`, registered in `ALERT_TYPES` in `app/seed.py`, digest
>    delivery by default — see the existing alert-type conventions).
> 4. **A linked profile that vanished upstream** (Etsy `is_deleted`, or missing from the
>    eBay list) keeps its last price and raises an immediate-class alert naming the profile:
>    drafts pushed with that id will fail, and margin is now running on an unverifiable
>    number. Do not clear the link automatically.
> 5. **Calculated marketplace profiles** are skipped with no alert (there is nothing to
>    import) — the Settings UI already shows them as "calculated on Etsy".
> 6. **Manual trigger.** `POST /shipping-profiles/refresh-prices/{platform}` runs the same
>    routine on demand; a "Refresh from Etsy / eBay" button on
>    `frontend/src/components/settings/ShippingProfileSettings.tsx` calls it and shows
>    `last_shipping_price_refresh_at` per platform with the count of profiles changed.
>
> **Work:**
> - `services/shipping_price_sync.py` with `refresh(platform) -> RefreshResult` (changed,
>   unchanged, skipped_calculated, missing_upstream, error) plus the scheduler hook and the
>   connection columns' migration.
> - Auth failures: do not add a second backoff counter; if the fetch raises
>   `PlatformAuthError` let the existing sync loop's counter own it (the order sync in the
>   same tick will have failed the same way).
> - Rate limit: on `PlatformRateLimitError` skip this refresh and leave
>   `last_shipping_price_refresh_at` unset so the next tick retries.
> - `record_price_change` is also called by the manual import endpoint from prompt 1 and by
>   the Settings edit path, so the event log is complete rather than sync-only.
> - Settings → Pricing (or the shipping-profile page): a collapsible "Price history" per
>   profile reading the events table.
> - Tests: refresh writes only changed prices; records events with the right source; one
>   alert per refresh with the changed profiles named; deleted-upstream raises the immediate
>   alert and keeps the price; calculated skipped silently; rate-limit leaves the timestamp
>   unset; the tick honours `shipping_price_refresh_hours`; manual endpoint works without
>   waiting for the tick.
> - CHANGELOG `[Unreleased]` entry.
