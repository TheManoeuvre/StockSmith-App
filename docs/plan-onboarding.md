# Plan — New-user onboarding & in-app documentation

Written 2026-11-19 against 0.27.0. Supersedes the scoping pass in `docs/roadmap.md`
("New-user onboarding"), which remains the record of *why* this sits at 1.0; this document
is the *what*.

---

## 1. Decisions recorded

| Question | Decision | Consequence |
|---|---|---|
| Who is StockSmith for? | **Public distribution, as a courtesy** — no support commitment, no operated services | Onboarding and docs must be fully self-serve: nobody can ask. |
| Marketplace credentials | **Per-user developer apps (decided 2026-11-19)** | StockSmith takes on no custody of anyone's tokens or data and runs no servers. Etsy/eBay setup stays developer-grade; onboarding answers it with guides, not a "Connect" button — see §8. |
| Which market? | **Anywhere; UK defaults first** (revised 2026-11-19) | Dates follow the computer's locale and currency is a setting. UK defaults (Etsy/eBay UK fees, Royal Mail postage) are seeded because that's where StockSmith is tested; every UK-specific default is marked in §4 so other markets can be added. |
| Sample data? | **Yes — and not necessarily 3D-print related** | A discardable sample workspace (§6). A non-3D business also proves the app is general, which public distribution needs. |
| Where do docs live? | **Inside the app** | Bundled with the frontend, versioned with the release, works offline (§7). Not top priority; scaffolding first. |
| Process | **Every feature and PR considers onboarding & docs impact** | PR template section + `CLAUDE.md` rule (§9). |

---

## 2. Readiness tiers — the minimum configuration

What "set up" means, in dependency order. The checklist (§5) is built from these.

| Tier | Outcome | Requires | Status on a fresh install |
|---|---|---|---|
| 0 | App runs | — | ✅ already true |
| 1 | "My stock is in here and it's right" | Materials, opening count | Empty |
| 2 | "I know what I can make and what it costs" | Products + BOMs, default kitting BOM | Empty |
| 3 | "Orders flow in" | Store connected, sync start date, listings adopted | Empty |
| 4 | "My margins are right" | Fees confirmed, shipping profiles | Fees seeded (UK), shipping empty |

Tiers 1–2 are the real minimum. Manual orders make tier 3 optional for someone not on a
supported marketplace.

---

## 3. Page-by-page on a fresh install

| Page | Needs before useful | Empty-state next action |
|---|---|---|
| Dashboard | Nothing of its own | Setup checklist until tier 2 is complete |
| Materials | Categories, units (seeded) | "Add your first material" / "Import from CSV" |
| Products | ≥1 material | "Add materials first" if none, else "Add your first product" |
| Purchases | Suppliers, materials | "Record a supplier order" |
| Stock Take | Materials | "Count your opening stock" — doubles as tier-1 opening balance |
| Locations | Nothing (optional) | "Where do you keep things?" with Add a location / Generate a range; optional checklist step after opening stock |
| Orders | Store connection or manual entry | "Connect a store" / "Add an order by hand" |
| Insights | Orders + product costs | Explain that it fills as orders arrive; link to tier 2 |
| Settings → Stores & sync | — | Per-store connect guide (§7) |
| Settings → Pricing & fees | Seeded | Prompt to confirm rates match the user's account |
| Settings → Shipping & packaging | — | Shipping profiles + default kitting BOM |
| Settings → Lists | Partly seeded | See §4 |

---

## 4. Defaults and seed data

Seeds live in `backend/app/seed.py`, guarded on "table is empty" so a deleted default
never returns.

**Already seeded:** general settings (GBP), backup schedule (on), notification types,
margin fee config, Etsy/eBay UK fee components *(UK-specific)*, units (g, ml, each),
material categories (filament, resin, pigment, hardware, packaging, blanks, other).

**Proposed additions:**

- **Category & flag explanations.** The behaviour flags (`consumed_on_failed_build`,
  `auto_kitting_per_order`, `tracks_material_type`, …) are the concept users won't grasp.
  Add a description to each seeded category and help text to each flag in Lists.
- **Material types** — a handful of examples, because type drives substitution and an
  example teaches that faster than prose. Deletable.
- ✅ **Shipping profiles** — a Royal Mail starter set *(UK-specific)*, new installs only. See §12.
- **Not seeded, deliberately:** suppliers, manufacturers, product categories. Business-
  specific; seeded rows are clutter to delete.

**Decided (2026-11-19) — ask "what do you make?" on first launch.** ✅ Built. Five presets
plus a "Something else" fallback, defined in `backend/app/services/business_presets.py`:

| Preset | Categories | Extra unit |
|---|---|---|
| 3D printing & resin | the original seven, unchanged | — |
| Candles, soap & bath | wax, soap base, fragrance, colourants & additives, wicks, vessels, labels, packaging, other | — |
| Jewellery & beading | findings, beads, chain & wire, stones & charms, cord & thread, packaging, other | cm |
| Sewing, knitting & textiles | fabric, yarn, thread, interfacing & wadding, notions, labels & tags, packaging, other | m |
| Paper goods & print | paper & card, sticker & vinyl sheets, envelopes, embellishments, packaging, other | — |
| Something else | raw materials, components, consumables, packaging, other | — |

Each preset also seeds example material types. Rules: the choice is stored in
`general_settings.business_type` (NULL = not asked yet); it can only be applied while no
materials exist; existing installs with data are stamped `3d_printing` by migration
`d2e7a4c19f60` and never see the picker. Changing business type after setup is not
supported — categories are edited in Settings → Lists instead.

---

## 5. Onboarding flow

1. **First-launch choice:** Start fresh · Restore from backup (new PC) · Explore sample data.
   ✅ Partly built: the business-type picker (§4) offers "Restore a backup instead".
2. **Basics:** confirm currency, ✅ business type, sync start date defaulting to today.
3. **Setup checklist** — on the Dashboard, resumable, dismissable. Steps follow §2's tiers.
   Each step:
   - completes itself from real data (e.g. "≥1 material has stock"), never a manual tick;
   - deep-links to the page that does the work;
   - can be skipped where optional (stores, shipping).
   Backed by one endpoint (e.g. `GET /onboarding/status`) that computes each step's state,
   so the frontend never re-derives it.
4. **Per-page intro** — on first visit: 2–3 sentences + "Learn more" into the in-app docs.
   Dismissed per page.
5. **Promote buried tools** — "Suggest profiles from Etsy" and the Etsy backfill become
   checklist steps under tier 3.

**Re-viewing:** a "?" in every page header reopens that page's intro and links its doc;
Settings → Getting started reshows the checklist and resets intros.

**Deliberately not:** step-by-step coach-mark tours anchored to specific buttons. The UI
changes most releases; anchored tours break silently.

---

## 6. Sample workspace

- **Separate database file**, never mixed with real data. Entering sample mode switches the
  backend to `sample.db`; leaving it discards it. A persistent banner says "Sample data —
  nothing here is real".
- **Proposed business: a small candle maker** (to confirm). It exercises the app well:
  - wax in grams with substitutable types (soy / coconut / paraffin) → material types;
  - fragrance oil in ml → a second unit;
  - wicks and vessels counted "each", vessels with colours;
  - products with scent × size variants and BOM overrides;
  - packaging consumed per order → kitting;
  - a failed pour → failed-build consumption;
  - an outstanding supplier order, a stock take due, ~3 months of orders for Insights.
- **Needs investigation:** whether orders can carry an Etsy/eBay channel with no platform
  connection, or whether sample orders must be manual-channel only.
- Generated by a script from a fixture, so it is rebuilt against the current schema each
  release rather than shipped as a stale `.db`.

---

## 7. In-app documentation

**Scaffolding (first build):**

- Markdown files under `frontend/src/docs/`, bundled at build time — offline, and always
  matching the installed version.
- A `/help` route rendering them, with a sidebar and search.
- A `<HelpLink doc="materials/categories" />` component used by page "?" buttons, intros
  and empty states.
- A test that every `doc="…"` slug referenced in the code resolves to a file, so a renamed
  page can't leave a dead link.

**Content (written over time, highest value first):**

1. Concepts & glossary — materials vs products, BOMs & variants, kitting, category flags,
   how stock is calculated, allocation, ABC classes, listing adoption.
2. Getting started — the §2 tiers in user language.
3. Connecting a store — Square (short), Etsy, eBay (long, with screenshots).
4. Moving to a new PC — backup & restore.
5. Troubleshooting — SmartScreen, antivirus, Reconnecting screen, log location (move from
   `README.md`).
6. CSV import reference — templates and every column.
7. One page per app page — what it's for, common tasks.

`README.md` then becomes developer-facing only.

---

## 8. Blockers carried from the roadmap

- **Marketplace credentials — decided: per-user developer apps.** StockSmith is shared as a
  courtesy and its author takes on no additional responsibility, which rules out shared app
  credentials (platform review, shared quotas, an operated token-exchange service, holding
  other people's account access, application-level eBay data-deletion obligations). It also
  rules out any StockSmith-operated relay or hosted backend (`plan-always-on-sync.md` options
  C-operated and D); a relay each user deploys to their own account remains possible.

  What this means for onboarding:
  - **Square and manual orders are the easy path** and should be presented first.
  - **Etsy and eBay are an advanced path**, labelled honestly as needing a developer account
    and roughly how long it takes, so nobody starts it by accident.
  - Their guides (§7 item 3) become the most important docs in the app: step-by-step, with
    screenshots, covering the developer app, the https redirect relay and eBay signing keys.
  - The connection forms should validate each pasted value and say *which* one is wrong —
    with no support channel, the error message is the support.
  - The existing per-user path is unchanged; nothing to build on the credential model itself.
- **CSV for BOMs and variants.** Missing; a shop with an existing catalogue hand-keys them.
  Either build it or document hand entry as the supported route.

---

## 9. Process — onboarding & docs impact on every change

Every PR answers, in the template's "Onboarding & docs impact" section:

- Does a new install need a default or seed for this?
- Does it change the setup order or add a checklist step?
- Does it need or change an empty state, page intro or help doc?
- Does it make any existing doc, intro or seed wrong?

"None" is a valid answer; a blank is not. `CLAUDE.md` carries the same rule for AI-assisted
work.

---

## 10. Build order

1. ✅ This plan; PR template + `CLAUDE.md` process rule.
2. ✅ Docs scaffolding (§7). Pages live in `docs/help/*.md` — readable on GitHub as-is — and
   are bundled into the app's **Help** page (`frontend/src/lib/help.ts`). Tests fail the build
   on a page missing from the sidebar, a broken cross-link or anchor, or an index that doesn't
   list every page. First pages: getting started, concepts, material categories, Square /
   Etsy / eBay, backup & restore, troubleshooting.
3. ✅ §4 category question decided and built (business-type picker). ✅ §8 credentials
   decided: per-user apps.
4. ✅ Flag help text: a tooltip on every category switch, and the Lists description links to
   the help page. Seed additions beyond the presets (shipping profiles) — open, see §11.
5. ✅ Empty states on Materials, Products, Purchases, Orders and Stock takes (§3). Insights
   not yet.
6. ✅ `/onboarding/status` + Dashboard checklist (§5.3). Seven steps; existing installs start
   with it hidden; Help's home page brings it back.
7. ✅ The first-launch screen offers all three: choose a business type (fresh), "Look around
   with sample data first", or "Restore a backup instead".
8. ✅ "?" beside every page title, targets in `PAGE_HELP`. Per-page first-visit intros not
   built — the "?" and the empty states cover the same need with less to maintain; revisit if
   users miss them.
9. ✅ Sample workspace (§6). `bootstrap.py` opens `<data dir>/sample/` instead of the real
   database when `workspace.json` asks for it, and builds the sample on first entry through
   the app's own functions (`services/sample_data.py`: Ember & Fig Candle Co., 13 materials,
   3 products, 4 variants, 23 orders over 90 days, a purchase in transit). Leaving deletes the
   folder at the next start. Restores are refused inside the sample. Store sync for the real
   shop pauses while the sample is open — said in the banner.
10. ✅ Store-connection guides written; Square's dialog and the Etsy/eBay developer-app card
    link to them. Per-field validation on the connection forms — not started.

---

## 11. Decisions on the open questions (2026-11-19)

| # | Question | Decision | Status |
|---|---|---|---|
| 1 | Licence | **MIT** | ✅ `LICENSE`, README and Settings › About updated |
| 2 | Sample workspace | **Separate database file** | See build order item 9 |
| 3 | Default "Import orders from" date | **The day a shop is connected** (reset when a different shop or Square location connects). Historical orders carry no cost of goods and arrive as a backlog of "awaiting" work | ✅ Built; help tells users with orders still to send to move it back |
| 4 | Square dialog environment | **Production by default** | ✅ |
| 5 | Royal Mail starter set | **Seed it**: Large Letter, Small Parcel, Medium Parcel. **Re-check prices quarterly** | See §12 |
| 6 | CSV for BOMs and variants | **Build it** | See build order |
| 7 | GitHub as the public home | **Yes** — public repository with Issues | ✅ Help links to Issues |
| 8 | Unverified portal steps | Author will walk through them | Open |
| 9 | Date locale | **Follow the computer.** "UK-only" was a misstatement: StockSmith is for sellers anywhere; the UK is where it's tested | ✅ Range formatting now locale-correct (it read "15–Aug 18" on US machines); test fixed |
| 10 | Code signing | **Not now** | — |

---

## 12. Seed data that goes stale

Some defaults are real-world prices that change. Each is listed here with where it lives and
when it was last checked. **Re-check quarterly** (January, April, July, October) and update the
seed, its "last verified" comment and this table together. A changed seed only affects new
installs; existing installs keep the values they have, which users edit in Settings.

| Seed | Lives in | Last verified |
|---|---|---|
| Etsy UK fee components | `backend/app/seed.py` `_FEE_COMPONENTS` | July 2026 |
| eBay UK fee components | `backend/app/seed.py` `_FEE_COMPONENTS` | July 2026 |
| Royal Mail 2nd Class: Large Letter ≤100g £1.55, Small Parcel ≤2kg £4.60, Medium Parcel ≤2kg £7.50 (stamp prices from 5 Oct 2026; online prices conflicted between sources and Royal Mail's site refuses automated reads, so check them by hand) | `backend/app/seed.py` `_SHIPPING_PROFILES`, `ROYAL_MAIL_VERIFIED` | 19 Nov 2026 |
