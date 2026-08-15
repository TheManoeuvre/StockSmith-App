# StockSmith — Roadmap to 1.0

## How to read this

This is a **map, not a plan**. Every theme below points at the document that actually
specifies it; nothing is duplicated here, because a second copy of a plan is a copy that
goes stale. The conventions this repo already uses:

- `docs/plan-*.md` — a planning pass for one theme, written before the code and kept
  updated with ✅ marks as its build order lands. These are the source of truth.
- `docs/backlog.md` — informal improvements *not yet scheduled into a plan doc*. It is
  deliberately unordered.
- This file — the ordering between themes, and what "1.0" is supposed to mean.

Written 2026-08-15, against 0.6.3.

---

## Where things stand

| Theme | State | Source of truth |
|---|---|---|
| Core inventory, BOM, variants, costing | Shipped | `plan-phase0-phase1.md` |
| Orders, allocation, kitting, returns | Shipped | `plan-marketplace-integrations.md` §4 |
| Etsy + eBay sync, quantity push, cross-platform stock | Shipped — all 11 build-order steps ✅ | `plan-marketplace-integrations.md` |
| Desktop packaging, updater, backup/restore | Shipped | `README.md`, `CHANGELOG.md` |
| Draft listing creation (new product → marketplace) | **In flight** — PR #22 (stages 4, 5, 7) | PR #22 |
| Stock take & ABC classification | **In flight** — Phase A built on `claude/stock-take-planning-ycowuy`, Phase B ahead | `plan-stock-take.md` (on that branch) |
| Backlog quick wins (CI, line endings, Build-now link) | **In flight** — PR #23 | `backlog.md` |
| Always-on sync | Planned, unbuilt (step 1 of 5 shipped in 0.6.3) | `plan-background-sync.md`, `plan-always-on-sync.md` |
| **New-user onboarding** | **Unplanned — this document is where it enters** | below |
| Print-queue management | Never started, never planned | `plan-phase0-phase1.md` names it as a later phase |
| Shipping automation | Parked deliberately, not deferred | `plan-marketplace-integrations.md` §2 |

---

## Order of work

**1. Land what's in flight.** Three parallel workstreams is already the ceiling. Nothing
new should start against `platforms/`, `listing_*`, the products/materials schemas or the
dashboard schema until PR #22, PR #23 and the stock-take branch are merged.

**2. Always-on sync.** The one shipped-but-hollow capability: auto-sync exists and only
runs while the window is open. `plan-background-sync.md` specifies the tray; the new
`plan-always-on-sync.md` sits above it and asks whether the tray is the right shape at
all, now that both marketplaces expose webhooks. Read the second before building the
first — it changes step ordering, though not the tray's own design.

**3. Correctness backlog.** Whatever `backlog.md` still holds after PR #23. The eBay
real-listing-id entry specifically must wait for PR #22, which rewrites the surrounding
invariant.

**4. New-user onboarding.** Scoped below. Target: the release that becomes 1.0.

**5. Undecided, needs a call before 1.0 scope freezes.** Print-queue management is the
one original-roadmap phase never begun — `builds.py` records a build after the fact
(including failed prints), and the dashboard lists orders short on stock, but there is
nothing between them: no queue, no batching of orders into a print job, no
work-in-progress state. It is a genuinely new capability, not a gap, so it belongs either
in 1.0's scope or explicitly after it. Shipping automation is different: it was **closed
off on purpose**, because the eBay Marketplace Account Deletion exemption is claimed on
the basis that no buyer address is ever stored. Reopening it means building the
notification endpoint and re-declaring. That's a strategy decision, not a task.

---

## New-user onboarding

**Target: 1.0. Not yet planned — this section is the scoping pass, not the plan.**

### Why it sits at 1.0

Onboarding is the one feature whose scope is defined by everything else in the product.
Every setting added between now and 1.0 is another thing a new install starts without,
and every capability added is another dependency ordering a new user cannot guess. Built
early, it is rewritten by each subsequent release; built at 1.0, it captures the finished
shape once. That is the argument for the placement, and it holds as long as the release
before 1.0 is genuinely feature-complete — see "what would move it earlier".

### What a new install actually faces today

Findings from reading the code, not from assumption:

1. **First-launch credential handoff already works and is invisible.** `app/bootstrap.py`
   generates a password, a hash and a Fernet key into `config.json`; `app/main.py`'s
   `/bootstrap-info` hands it to the Tauri shell exactly once and then permanently 404s.
   This is the only piece of onboarding that exists, and it needs nothing.

2. **Seeding covers three things and stops.** `app/seed.py` seeds Etsy/eBay UK fee
   components, general settings and backup settings (scheduling on by default). Not
   seeded, and all required before the app does anything useful: material types, colours,
   manufacturers, suppliers, shipping profiles, listing profiles, materials, products,
   BOMs, the default kitting BOM — and, once the stock-take branch lands, product types
   and ABC tiers.

3. **There is no guided path of any kind.** No wizard, no checklist, no sample data, no
   first-run tour. The empty state is a handful of "No backups yet"-style strings. A new
   user lands on a dashboard reading £0 with no indication of what to do first.

4. **The dependency ordering is real and unguessable.** Reference data before materials;
   materials before a BOM; a BOM before buildability means anything; a kitting BOM before
   packaging capacity is right; `sync_start_date` before the first sync or you import
   years of history; connect *then* adopt listings (`docs/listing-adoption.md`) to link an
   existing catalogue; listing profiles before any product can be drafted — PR #22 notes
   its own feature is inert until one exists ("every product reports 'no listing profile
   applies' and the draft button stays disabled").

5. **The marketplace setup is developer-grade, and this is the actual wall.** Each user
   must, today, register their own developer app on Etsy (client ID/secret, plus a
   redirect URI that may reject loopback — hence the `public_base_url` override), register
   their own eBay keyset and RuName, stand up an **https redirect relay** because eBay's
   portal rejects anything else (the current install runs a Cloudflare Worker for exactly
   this — `plan-marketplace-integrations.md` build-order step 5), and mint eBay Ed25519
   signing keys for the digital-signature-gated APIs (`platform_credential.py` carries the
   keypair). No non-developer completes this sequence. It is not a UX problem and no
   wizard fixes it — see "the decision this forces".

6. **Bulk import stops short of the hard part.** `csv_io.py` exports and imports materials
   and products. There is no CSV path for BOM lines, variants, variant overrides or
   kitting BOMs — which is precisely the data a shop with an existing catalogue has the
   most of, and the part that is most tedious to key in by hand.

7. **The good tools exist but are buried.** "Suggest profiles from Etsy"
   (`listing_profile_backfill.py`, which groups a catalogue's listings into a handful of
   real profile combinations) and the Etsy description/price/photo backfill
   (`etsy_backfill.py`) are two of the strongest onboarding assets in the codebase, and
   both live inside Settings → Integrations where a new user has no reason to look.

8. **A second machine is not an onboarding path.** The backend binds to `127.0.0.1`
   (README, "Known limitation"), so moving to a new PC means restore-from-backup, which
   sits under Settings → Backups and is documented nowhere a new user would read.

### What the item has to cover

Sketch only — the planning pass decides the shape:

- A first-run path that gets someone from empty to "my stock is in here and it's right",
  in the dependency order above rather than the Settings-page order.
- A resumable checklist rather than a linear wizard, because steps 4 and 5 above have
  genuinely different owners and timescales (typing in materials vs. waiting on an eBay
  developer keyset).
- The two existing backfill tools promoted out of Settings into that path.
- CSV coverage for BOMs and variants, or an explicit decision that hand-entry is the
  supported route.
- Empty states that state the next action, not the absence of data.
- A documented new-machine path (restore, or export/import).
- Whatever the marketplace-credential decision below turns out to be.

### The decision this forces

Finding 5 is the one that cannot be designed around, and it is shared with
`plan-always-on-sync.md`: either every user registers their own developer apps — which
caps StockSmith at users who can follow a developer portal — or StockSmith ships **shared
app credentials** and becomes a distributed application, with platform review, quotas
against one keyset, and the eBay data-deletion exemption applying at the application
level rather than to one shop. That is a strategic choice with legal and operational
weight, it is the single biggest determinant of what onboarding even means, and it wants
deciding well before 1.0 rather than during it.

### What would move it earlier

Any of: a second person needing to run StockSmith; a decision to distribute it publicly;
or shared app credentials landing early for the always-on work, which would take the
sharpest edge off finding 5 and make an earlier, smaller onboarding pass worthwhile.

---

## Proposed definition of 1.0

Offered for confirmation, not settled:

1. Everything in flight merged and released.
2. Always-on sync working, in whatever shape `plan-always-on-sync.md` settles on.
3. `backlog.md` empty of correctness items (nice-to-haves may survive).
4. A decision recorded on shared vs. per-user marketplace app credentials.
5. Onboarding built against the finished feature set.
6. A print-queue decision — in scope, or explicitly post-1.0.
