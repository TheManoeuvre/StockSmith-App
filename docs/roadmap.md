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

Written 2026-08-15 against 0.6.3; **fully reconciled against 0.17.0 on 2026-09-18**. The
reconcile is recorded in "What changed since 0.6.3" below, so the gap between the original
reading and the current one stays visible rather than being quietly overwritten.

---

## Where things stand (at 0.17.0)

| Theme | State | Source of truth |
|---|---|---|
| Core inventory, BOM, variants, costing | Shipped | `plan-phase0-phase1.md` |
| Orders, allocation, kitting, returns | Shipped | `plan-marketplace-integrations.md` §4 |
| Etsy + eBay sync, quantity push, cross-platform stock | Shipped — all 11 build-order steps ✅ | `plan-marketplace-integrations.md` |
| Desktop packaging, updater, backup/restore | Shipped | `README.md`, `CHANGELOG.md` |
| Draft listing creation (new product → marketplace) | **Shipped** — PR #22 merged 16 Aug; variations followed | `CHANGELOG.md` 0.16–0.17 |
| Stock take & ABC classification | Shipped — both phases, plus grouped count sheets and made-to-order exclusion | `plan-stock-take.md` |
| Receiving by line | Shipped — deliveries are their own records, dated and costed individually | `CHANGELOG.md` |
| Listing-push rate reduction | Shipped — stages 1–4 (0.12.1); 5–7 pending on the usage numbers | `plan-listing-push-rate-reduction.md` |
| Notifications (in-app, Windows toast, Pushover) | Shipped 0.13.0, refined through 0.15.0 | `CHANGELOG.md` |
| Fallback materials & order-line substitution | Shipped 0.14.1–0.16.0 | `CHANGELOG.md` |
| Shipping-profile ↔ marketplace linking | Shipped 0.16.0 | `plan-shipping-profile-marketplace-link.md` |
| Replacement parcels & postage labels | Shipped 0.17.0 — Etsy label detection still best-effort | `CHANGELOG.md`, `backlog.md` |
| Always-on sync — tray | Shipped — tier 1 (steps 1–6 ✅); watchdog (tier 2) still unbuilt | `plan-background-sync.md` §6 |
| Always-on sync — webhooks | **Next up** — recommended, not started; no webhook code exists | `plan-always-on-sync.md` §4 |
| **New-user onboarding** | **Unplanned — this document is where it enters** | below |
| Print-queue management | Never started, never planned | `plan-phase0-phase1.md` names it as a later phase |
| Shipping automation | Parked deliberately, not deferred | `plan-marketplace-integrations.md` §2 |

Nothing is in flight against `main` beyond two open feature PRs (#124 variable-pricing
groups, #125 Etsy variation defaults) and two dependency bumps.

---

## What changed since 0.6.3

The original reading of this document is eleven releases old. What it got wrong, and what
it could not have known:

- **"In flight: PR #22 and PR #23" is gone.** Both merged in August. The draft-listing
  feature they carried is not only shipped but has been extended twice since — variation
  drafts, and shipping-profile-driven postage on the draft.
- **The three-parallel-workstreams ceiling no longer binds.** The order-of-work rule that
  nothing new should start against `platforms/`, `listing_*` or the products/materials
  schemas was written to protect those two PRs. It has been overtaken; a great deal has
  landed in all three areas since.
- **Always-on sync split in two, and half of it shipped.** The tray's whole tier 1 is
  built and released (PID reaping, single-instance, tray + hide-on-close, sidecar
  supervision, autostart, sync-health visibility). The webhook relay — the option
  `plan-always-on-sync.md` rates best — has not been started. No webhook endpoint,
  registration or signature-verification code exists anywhere in the backend.
- **Push reconciliation shipped early, out of the backlog.** `services/listing_reconcile`
  (0.12.1) is `plan-background-sync.md`'s step 8 and the backlog's own entry, delivered as
  part of the rate-reduction work instead. Both have been struck off.
- **Seven themes arrived that this document never named**, because they weren't planned
  when it was written: notifications, fallback materials, order-line substitution,
  shipping-profile linking, replacement parcels and marketplace label capture, the
  listing-push rate reduction, and two rounds of redesign. They are in the table above now.
- **The backlog did not empty; it turned over.** Twenty-seven entries at the last count,
  two of which this reconcile deleted as done. Its correctness items are still the thing
  standing between here and 1.0's condition 3.

---

## Order of work

**1. Webhook relay.** The one clear next step, and the only always-on work with a
recommendation already attached (`plan-always-on-sync.md` §4.2): it collapses the
sale-to-sync gap from 15 minutes to seconds, extends a component that exists for another
reason, costs nothing, and holds no token. The tray it sits on top of is done.

**2. Correctness backlog.** `backlog.md`'s first two groups are the ones that cost real
money or real trust today, and one of them has got worse since it was written:

- The **Etsy structural push failure** is now retried *hourly* by the reconciliation sweep
  that shipped in 0.12.1, which has no concept of a permanent failure. Seven pushes that
  cannot ever succeed are burning daily Etsy budget on a loop. This is the single
  highest-value backlog entry and it was not urgent when this document was last written.
- **Disconnect silently disabling auto-sync** (`platforms.py:674`) is still live, still
  invisible, and still the first thing anyone tries when a platform looks stuck.
- **eBay refunds are never recorded** — `refunded_amount` appears nowhere in the eBay
  adapter, so partially refunded eBay orders still count returned money as profit.

**3. The eBay real-listing-id change.** Unblocked: it was waiting on PR #22, which merged
in August. It stays its own commit for the reason the backlog gives.

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

**Re-checked at 0.17.0:** every finding below still holds. Nothing in eleven releases has
added a guided path, a checklist, sample data or a first-run tour, and no CSV route for
BOM lines or variants has appeared. Two findings moved slightly and are marked inline.

### Why it sits at 1.0

Onboarding is the one feature whose scope is defined by everything else in the product.
Every setting added between now and 1.0 is another thing a new install starts without,
and every capability added is another dependency ordering a new user cannot guess. Built
early, it is rewritten by each subsequent release; built at 1.0, it captures the finished
shape once. That is the argument for the placement, and it holds as long as the release
before 1.0 is genuinely feature-complete — see "what would move it earlier".

The eleven releases since this was written are the argument's own evidence: notifications,
fallbacks, substitutions, shipping-profile linking and replacement parcels each added
settings and dependencies a first-run path would have had to be rewritten for.

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
   BOMs, the default kitting BOM, product categories and ABC tiers.

3. **There is no guided path of any kind.** No wizard, no checklist, no sample data, no
   first-run tour. The empty state is a handful of "No backups yet"-style strings. A new
   user lands on a dashboard reading £0 with no indication of what to do first.

4. **The dependency ordering is real and unguessable.** Reference data before materials;
   materials before a BOM; a BOM before buildability means anything; a kitting BOM before
   packaging capacity is right; `sync_start_date` before the first sync or you import
   years of history; connect *then* adopt listings (`docs/listing-adoption.md`) to link an
   existing catalogue; listing profiles before any product can be drafted. *Since 0.16.0
   this ordering got one step longer, not shorter: a product's shipping profile now has to
   be linked to an Etsy shipping profile or eBay postage policy before a draft ships
   correctly.*

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
   and products (and exports orders, purchases and stock takes). There is still no CSV
   path for BOM lines, variants, variant overrides or kitting BOMs — which is precisely
   the data a shop with an existing catalogue has the most of, and the part that is most
   tedious to key in by hand.

7. **The good tools exist but are buried.** "Suggest profiles from Etsy"
   (`listing_profile_backfill.py`) and the Etsy description/price/photo backfill
   (`etsy_backfill.py`) are two of the strongest onboarding assets in the codebase, and
   both live inside Settings → Stores & sync where a new user has no reason to look.
   *0.16.0 made the first of them stronger still — it now proposes shipping profiles too —
   and 0.17.0 moved it behind one more click, onto a per-store page.*

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

**Still undecided at 0.17.0**, and now on the critical path for two themes rather than
one: the webhook relay in step 1 above is the cheap version of the same question about
where StockSmith's trust boundary sits.

### What would move it earlier

Any of: a second person needing to run StockSmith; a decision to distribute it publicly;
or shared app credentials landing early for the always-on work, which would take the
sharpest edge off finding 5 and make an earlier, smaller onboarding pass worthwhile.

---

## Proposed definition of 1.0

Offered for confirmation, not settled. Re-checked at 0.17.0:

1. ~~Everything in flight merged and released.~~ **Met** — PRs #22 and #23 merged in
   August and have shipped in several releases since.
2. Always-on sync working, in whatever shape `plan-always-on-sync.md` settles on.
   **Half met** — the tray is shipped; the webhook relay it recommends is not started.
3. `backlog.md` empty of correctness items (nice-to-haves may survive). **Not met** —
   the correctness items in its first two groups are the gating set, led by the Etsy
   structural push failure now looping hourly.
4. A decision recorded on shared vs. per-user marketplace app credentials. **Not met** —
   no decision record exists in `docs/`.
5. Onboarding built against the finished feature set. **Not started.**
6. A print-queue decision — in scope, or explicitly post-1.0. **Not made.**
