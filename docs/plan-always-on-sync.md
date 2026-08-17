# StockSmith — Always-On Sync: Options Pass

## Status

Planning only. This sits **above** `docs/plan-background-sync.md`, which specifies the
tray in detail and remains valid as a design. This document asks the question that one
didn't: is a tray-resident desktop app the right shape at all, and what can be added
beyond it — in the cloud, cheaply, without a rewrite?

Written after re-reading `app/services/sync_scheduler.py`, `listing_push.py`,
`order_sync.py`, `app/models/platform_credential.py`, `app/models/backup_settings.py`,
`app/bootstrap.py` and `frontend/src-tauri/src/lib.rs`, plus a check of what Etsy and eBay
now offer for push notifications — which turns out to be the finding that reopens this.

**Two external facts are second-hand.** Etsy's webhook documentation could not be fetched
from this environment (egress-blocked), and Cloudflare's limits are point-in-time. Both
are marked below and must be confirmed before anything is built on them.

---

## 1. The question is really three questions

`plan-background-sync.md` treats "keep stock sync alive" as one problem. It is three, with
different answers, and conflating them is why the tray looks like a complete solution when
it isn't.

| # | Failure | When it bites | Does the tray fix it? |
|---|---|---|---|
| **A** | App closed, PC on — nothing syncs at all | Every evening the app isn't left open | **Yes.** This is exactly what the tray buys |
| **B** | App running, but a sale is invisible for up to 15 minutes | Continuously, whenever both marketplaces hold the last unit | No — polling interval is unchanged |
| **C** | PC off, asleep, or travelling | Overnight, weekends, away from the desk | **No.** Nothing local can |

The oversell risk `plan-marketplace-integrations.md` §3 identifies — "how long between the
sale happening on platform A and StockSmith's sync noticing it" — is B and C, not A. The
tray solves the most common annoyance and none of the actual overselling window.

---

## 2. What changed: both marketplaces now push

The original design polls because, when it was written, polling was the only option. That
is no longer true, and it is the single most consequential finding in this pass.

- **Etsy** now documents webhooks with order-lifecycle events (`order.paid`,
  `order.canceled`, `order.shipped`, `order.delivered`), endpoint registration, signing
  secrets for authenticity, and — per the summary available here — availability to
  personal as well as commercial apps. ⚠️ **Verify directly against
  developers.etsy.com before designing on it**: this environment could not reach the page,
  the `order.paid` event is recent (late 2025), and "available to personal apps" is exactly
  the kind of claim that turns out to have an approval step attached.
- **eBay** has the REST Notification API — topic subscription, HTTPS POST delivery, three
  retries, signed payloads — with `ORDER_CONFIRMATION` fired when checkout completes and
  payment clears, plus the legacy Platform Notifications path.

Both require the same thing: a public HTTPS endpoint that is up when the event fires.
StockSmith's backend binds to `127.0.0.1` and cannot be one. **But this project already
runs exactly such an endpoint** — the Cloudflare Worker standing in for eBay's OAuth
redirect (`plan-marketplace-integrations.md` build-order step 5), which exists because
eBay's portal refuses a non-https redirect. The infrastructure question is therefore not
"should we take on a cloud component" — one is already load-bearing — but "what else
should the one we have do".

---

## 3. Options

Ordered by how much trust each one asks for, which correlates with how much it fixes.

### A. Tray-resident app — `plan-background-sync.md`, unchanged

Fixes **A** only. Cost £0, no new trust boundary, no new infrastructure. Its four hazards
(§2a-2d of that doc) are all still real, and step 1 of its build order already shipped in
0.6.3.

**Verdict: still worth building, and still first.** Everything below assumes a desktop
process that can run unattended; the tray is what makes that true. It is also the only
option that needs no decision from anyone.

### B. Webhook relay — a queue the desktop drains

The Worker gains a second job: receive Etsy/eBay webhooks, verify the signature, and
append a minimal event record — platform, event type, external order id, timestamp.
Nothing else. The desktop app, whenever it is running, drains the queue and syncs
immediately instead of waiting for the next tick; on launch it drains whatever accumulated
while it was off.

- **Fixes:** B fully (sale → local sync in seconds). C **partially** — not the overselling
  itself, but the catch-up on return becomes immediate and precise rather than a watermark
  crawl.
- **Cost:** £0 at this volume. Cloudflare's free plan allows 100,000 requests/day (a shop
  doing hundreds of orders a day uses a rounding error of that) with free-tier storage for
  the queue. ⚠️ Point-in-time — re-check before relying on it.
- **Configuration:** a webhook URL and a signing secret pasted into each marketplace's
  developer portal, plus a shared secret so only this install can drain the queue.
  Comparable to the eBay RuName step already required.
- **Trust:** the relay never holds an OAuth token and never calls a marketplace. It learns
  *that* an order exists, not what is in it. **It must store no payload bodies** — only the
  four fields above — which keeps the eBay data-deletion exemption
  (`plan-marketplace-integrations.md` §2) intact by construction rather than by policy.
- **Risk:** an event arriving for an order the desktop already imported is a no-op — the
  unique `(platform, external_order_id)` constraint and `order_sync`'s watermark already
  make re-import idempotent. Delivery is best-effort on both platforms, so the interval
  poll stays as the backstop; webhooks make it faster, never authoritative.

### C. Cloud sync sentinel — the first option that fixes C

The Worker also holds refresh tokens, and on a cron schedule while the desktop is offline:
pulls new orders from both marketplaces, and pushes a decremented quantity to the *other*
platform's listing for the same product.

The reasoning that makes this coherent: **while the PC is off, marketplace sales are the
only thing that can change stock.** No builds, no adjustments, no material consumption. So
a small mirror of "last pushed quantity per listing" is sufficient to decrement correctly,
and the desktop re-pushes authoritative numbers on next launch — self-correcting by
design.

- **Cost:** still £0 of infrastructure. Cloudflare's free plan permits 3 cron triggers per
  account at a 1-minute minimum cadence — ample. ⚠️ Same point-in-time caveat.
- **Configuration:** materially harder. Token handoff has to happen from the desktop, and
  the Worker needs its own secret store.
- **Trust: this is the real cost.** Marketplace refresh tokens leave the machine. Today
  every token is Fernet-encrypted with a key in `config.json` that never leaves
  `%LOCALAPPDATA%`, and `backup_settings.py` records the same instinct explicitly —
  off-host backups go via a synced folder specifically so "backups get off the host machine
  without the app growing cloud credentials". Option C is a deliberate reversal of a
  standing design principle. It may be worth it; it should not be done accidentally.
- **Hazard: it breaks the single-writer assumption.** `sync_scheduler`'s docstring is
  explicit that per-platform `asyncio.Lock`s suffice because "a single process serves the
  whole desktop app". A cloud syncer is a second writer that cannot see those locks —
  precisely hazard §2d of `plan-background-sync.md`, arriving by a different door. It is
  survivable only if the sentinel's writes are strictly disjoint from the desktop's: it
  pushes quantities and never imports orders locally, and it stands down the moment the
  desktop is alive (a heartbeat, not a guess).

### D. Hosted backend — the topology reversal

Run the FastAPI backend and its database in the cloud; the desktop becomes the thin client
it was originally designed to be (`plan-phase0-phase1.md`: FastAPI + Postgres + Tailscale,
"an easy swap to cloud hosting later if needed").

Fixes A, B and C completely, and is the only option that also delivers multi-device and
mobile access. But it moves the database, the assets, the backups and the whole
config/credential model off the machine; free tiers that sleep are disqualified outright by
a scheduler that must not; and it means a real bill, real uptime responsibility and a
migration for existing installs.

**Verdict: not "little to no cost", and not a 0.x change.** It is the right answer *if*
StockSmith ever becomes multi-user or multi-device — which is why the onboarding decision
in `roadmap.md` and this one are the same decision wearing different hats.

### E. Tailscale remote access

Reaches the home PC from anywhere; free for personal use; the original architecture already
assumed it. Fixes nothing here — it is access, not availability. **Only relevant to "use
StockSmith from the sofa", not to "sync while the PC is off".** Worth keeping in mind for
onboarding, not for this.

### F. Scheduled wake, or a cheap always-on box

Windows Task Scheduler can wake a sleeping machine to run a task; a Raspberry Pi or retired
mini-PC can hold the sidecar permanently. Turns C into A, at £0 recurring, with no cloud and
no new trust boundary — but only for someone willing to administer it, and the wake trick
does nothing for a machine that is genuinely off or travelling.

**Verdict: not a product feature — a deployment note worth a paragraph in the README once
the tray exists.**

---

## 4. Recommendation

**Build A, then B. Defer C and D to one explicit decision.**

1. **Tray (A) as planned.** Self-contained, no decisions required, fixes the most frequent
   complaint, and it is the prerequisite for everything else. `plan-background-sync.md`'s
   build order stands; steps 1-2 are worth landing regardless.
2. **Webhook relay (B) next.** The best ratio in this document — it collapses the sale-to-
   sync gap from 15 minutes to seconds, costs nothing, extends a component that already
   exists for a different reason, and asks for no new trust because it never holds a token.
   It also stops the poll interval being the thing standing between the shop and an
   oversell.
3. **Sentinel (C) only if C-class failures actually hurt.** It is the only cheap answer to
   "PC off", but it moves tokens off-machine and puts a second writer in the system. Worth
   it if the PC is genuinely off for long stretches with both marketplaces live; not worth
   it to shave a morning's catch-up.
4. **Hosted (D) is a product decision, not a sync decision.** Revisit it only alongside the
   shared-app-credentials question in `roadmap.md`, since both turn on whether StockSmith
   is one shop's tool or a product other people install.

The honest summary: the tray is *not* the wrong approach — it is a correct answer to a
narrower question than it appeared to answer. Webhooks are the part that was missing.

---

## 5. Open questions

1. **How often is the PC genuinely off while both marketplaces are live?** This single
   answer decides whether option C is worth its trust cost or is over-engineering. Nobody
   should guess it — it is measurable from the existing `platform_sync_runs` table.
2. **Is putting refresh tokens in a Worker acceptable at all?** A clear "no" here is
   valuable: it removes C, and makes D the only route to fixing failure C, which in turn
   makes it a 1.0-scope question rather than a 0.x one.
3. **Self-hosted Worker, or one StockSmith operates?** A user deploying their own keeps the
   trust boundary at their own account but adds a developer-grade onboarding step — the
   exact wall `roadmap.md` identifies. One that StockSmith operates removes that step and
   takes on custody of other people's data. Same fork as shared app credentials.
4. **Does the webhook relay replace the poll, or supplement it?** Recommended:
   supplement, permanently. Both platforms document retries, which means they anticipate
   delivery failure, and a missed webhook with no backstop is a silently stale shop.
5. **Etsy's webhook terms** — approval, quotas, whether a personal app really qualifies.
   Blocking for B; answerable in an afternoon with portal access.

---

## 6. Verification

Option B is testable without touching a marketplace: the relay's signature verification,
its refusal to store payload bodies, and the drain endpoint's idempotency are all unit
work. End-to-end needs one real event per platform — a sandbox order on eBay (the
infrastructure from `plan-marketplace-integrations.md` step 5 already exists) and a
deliberate state change on a real Etsy listing.

Two properties matter more than the happy path, and both are about not making things
worse:

- **A duplicate or replayed event must change nothing.** Fire the same event twice and
  assert one order, one push.
- **A relay outage must be invisible.** Stop the Worker, let an order arrive, confirm the
  interval poll still finds it. If the shop is ever worse off with the relay than without,
  the design is wrong.
