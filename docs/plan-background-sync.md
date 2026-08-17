# StockSmith — Background / Tray Sync: Planning Pass

## Status

Sections 0-5 are the original time-boxed spike, produced as Phase 5 of the backlog
burndown to answer the questions `docs/backlog.md`'s "Background/tray process to keep
stock sync alive" entry left open before any code is written.

**Partly superseded by events:** §2a's mitigation (version identity in `/healthz`, refusing
to adopt a mismatched backend) shipped in 0.6.3. The PID file it pairs with did not.

**Sections 6-8 are new (2026-08-15)** and are the reason this is now a build plan rather
than a spike. They cover uptime on the specific machine this runs on — a Windows 11 Dell
OptiPlex also used for other things — after `docs/plan-always-on-sync.md` settled the
question of shape: build the tray first, since it is what makes an unattended desktop
process possible at all.

Read §6 before building §1. It does not change the tray's design, but it changes what
"autostart" has to mean, and it adds a component (§6d) the original spike didn't consider.

Read to write this: `frontend/src-tauri/src/lib.rs` (all of it),
`frontend/src-tauri/tauri.conf.json`, `frontend/src-tauri/capabilities/default.json`,
`frontend/src-tauri/Cargo.toml`, `backend/app/services/sync_scheduler.py`,
`backend/app/services/listing_push.py`, `backend/app/main.py`, `backend/build.ps1`,
`.github/workflows/release.yml`, plus Tauri v2 docs for the tray, autostart and
single-instance plugins and the reported NSIS-updater failure modes.

**Not verified by running anything.** Tray behaviour, autostart, and the update interaction
can only be confirmed against an installed build, which this spike did not produce. Every
claim below is either read from this repo's own source (reliable) or from Tauri
documentation and issue reports (treat as a hypothesis to test — see §2a, which is the one
that matters most).

**Context that changes the shape of this plan:** the thing that needs to stay alive is not
the Tauri shell — it is the **Python sidecar**. `sync_scheduler.start()` runs from
`app/main.py`'s lifespan, inside the backend process. The Tauri app is a supervisor and a
UI. So the requirement reduces to: *decouple the sidecar's lifetime from the window's*.
That reframing makes most of the design fall out, and it is why the recommendation below
is deliberately unambitious.

---

## 0. Current behaviour

- `on_window_event` (`lib.rs:211-223`) intercepts `CloseRequested` **only** to
  `taskkill /F /T` the sidecar PID. It does not call `api.prevent_close()`, so the default
  applies: closing the only window exits the app, and the backend dies with it.
- `spawn_sidecar_if_needed` (`lib.rs:70-99`) checks `GET /healthz` first and **reuses
  whatever answers on port 8000**, spawning nothing if something already does.
- `SidecarState` holds only the PID, deliberately: PyInstaller's onefile bootloader spawns
  a grandchild, so `CommandChild::kill()` leaves the real process running (`lib.rs:14-22`).
- No tray icon, no autostart plugin, no single-instance plugin. `tauri` has no
  `tray-icon` feature enabled.
- `sync_scheduler` polls **inbound order sync** only, per platform, when
  `auto_sync_enabled` is on. Outbound quantity push is event-driven via `listing_push`'s
  5-second debounce and has **no periodic retry at all** — a push that fails is never
  retried until something else changes that product's stock.

---

## 1. Recommended shape: a tray-resident app

Keep the existing architecture and change only when the process exits.

- Window close hides the window instead of quitting (`api.prevent_close()` + `window.hide()`).
- A tray icon with a menu: **Open StockSmith**, **Sync now**, **Quit**.
- Only **Quit** (and the existing sidecar-killing logic, moved there) actually exits.
- Autostart, opt-in, launching hidden.

The sidecar's lifecycle is otherwise untouched: still spawned by the shell, still killed by
`taskkill /F /T` on real exit. That keeps the PyInstaller grandchild problem solved the way
it already is.

**Rejected: running the backend as a Windows Service.** It would survive everything, but it
needs elevation at install time, and the backend's whole configuration model is per-user —
`app/bootstrap.py` writes `config.json` under `%LOCALAPPDATA%\StockSmith\`, and the SQLite
database and asset root live there too. A service running as SYSTEM or as a dedicated
account would resolve those paths somewhere else entirely. Not worth it for a
single-user desktop app.

**Rejected: a Scheduled Task running the backend independently.** Two owners of the same
port and the same SQLite file, with no UI affordance to stop it, and the shell's
"adopt whatever is healthy on 8000" logic would silently attach to it. Strictly worse than
the tray for the same benefit.

---

## 2. The four hazards this introduces

These are the reason the spike was worth doing. Each one is a way the naive version breaks.

### 2a. Updating while resident can orphan the sidecar — and the orphan gets adopted

This is the most consequential finding and the one to test first.

On Windows the app is exited automatically when the NSIS install step runs. The installer
knows about `StockSmith.exe`; it knows nothing about `stocksmith-backend.exe`, which is a
separate process with a different name that this app kills explicitly on exit. If the
installer terminates the shell without going through `CloseRequested`, **the sidecar
survives the update**.

That alone would be untidy. What makes it a correctness problem is `spawn_sidecar_if_needed`:
the new version boots, asks `GET /healthz`, gets `200 OK` from the *old* version's backend,
and adopts it. The user is then running a new frontend against an old backend, with no
indication anything is wrong — and, because the old backend is still running its scheduler,
possibly against an old sync implementation too.

There are also reports of the NSIS `CheckIfAppIsRunning` step failing outright against
tray-resident autostarting apps ([tauri#6984](https://github.com/tauri-apps/tauri/issues/6984)),
though that issue was closed as needs-repro, so treat it as a risk to reproduce rather than
an established fact.

**Mitigation, and it is cheap:** give `/healthz` an identity.

```
GET /healthz -> {"status": "ok", "version": "0.4.2", "pid": 1234}
```

`spawn_sidecar_if_needed` then refuses to adopt a backend whose version doesn't match the
shell's, kills it, and spawns its own. This is worth doing **regardless of whether the tray
work goes ahead** — the same adoption bug can already bite anyone running a dev backend on
8000 while launching the packaged app.

**Update (0.6.3): shipped, with one deliberate difference.** `/healthz` now carries the
build version and the shell refuses a mismatch — but rather than killing the stranger and
spawning its own, it stops and asks the user to close everything and re-run the installer.
That is the right call for a half-applied update in front of a human. It is the wrong call
at 3am on an unattended box, and §6c is where that tension gets resolved.

### 2b. Killing the shell leaks the sidecar

Today `CloseRequested` is the only path that kills the backend, and closing the window is
the normal way to quit. Once close means *hide*, the only remaining exit is the tray's Quit
— and anything that bypasses it (Task Manager, a crash, the installer in §2a) leaves an
orphan holding port 8000, which the next launch adopts.

**Mitigation:** write the sidecar's PID to `%LOCALAPPDATA%\StockSmith\backend.pid` on spawn,
and on startup reap any live process recorded there before probing the port. Combined with
the version check in §2a this makes adoption safe in both directions.

### 2c. Single-instance stops being optional

Autostart plus a Start-menu launch is two shells. Both would try to spawn or adopt the
sidecar, and both would add a tray icon. The plugin must be registered **first**, before
every other plugin, and its callback should show and focus the existing window — which is
exactly the affordance a hidden tray app needs anyway.

### 2d. `sync_scheduler`'s single-process assumption is load-bearing

Its module docstring is explicit: *"A single process serves the whole desktop app, so plain
module-level state (the lock registry, the running tasks) is sufficient — no need for
anything cross-process."* Those per-platform `asyncio.Lock`s are what stop a manual "Sync
now" and a background tick running `commit_sync` concurrently. Two backend processes would
each hold their own locks and neither would see the other, so the same orders could be
imported twice.

Nothing in the tray design creates a second backend *deliberately* — but §2a and §2b both
do so accidentally, which is what turns a tidiness problem into a data problem. Fixing those
two protects this assumption; it does not need to be relaxed.

---

## 3. Autostart

Opt-in, in Settings, default off. Not an install-time default: something that silently adds
itself to startup is the kind of behaviour users resent, and this app's value doesn't depend
on it.

- `tauri-plugin-autostart`, initialised with an argument (`--hidden`) so a boot launch
  doesn't pop a window in the user's face.
- Rust exposes `enable()` / `disable()` / `is_enabled()` through `ManagerExt`; the frontend
  needs the `autostart:allow-enable`, `autostart:allow-disable` and
  `autostart:allow-is-enabled` permissions added to `capabilities/default.json`.
- The Settings toggle should read `is_enabled()` rather than storing its own copy, so it
  stays truthful if the user removes the entry through Windows' own startup settings.
- `--hidden` also needs handling in the single-instance callback: a *second* launch should
  show the window even if the first was hidden.

**Two things about this plugin that §6 depends on:**

- **It writes `HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Run`.** That is
  a *logon* trigger, not a boot trigger. Nothing in it runs while the machine sits at the
  lock screen with nobody signed in — which is the state a Dell OptiPlex is in after an
  overnight Windows Update restart, and after every shutdown. This is the single most
  important fact in this document for the uptime goal, and it is why §6 exists.
- **There is a standing report that the Run entry disappears after the first boot**
  ([plugins-workspace#771](https://github.com/tauri-apps/plugins-workspace/issues/771),
  open since Nov 2023, no documented resolution found). Symptom: `enable()` writes the
  key, the app starts once at the next boot, the key is then gone. Treat as unverified but
  plausible, and **test it explicitly over two reboots** — an autostart that silently
  works once is worse than one that never worked, because nobody goes looking.

Because the Settings toggle reads `is_enabled()` live rather than caching, that failure at
least becomes visible in the UI rather than only in behaviour. Verify it that way.

---

## 4. Scope of the background work

Worth separating two things the backlog entry runs together.

**Keeping the existing scheduler alive** is what the tray buys, and it is a real win: today
auto-sync only runs while the app happens to be open, so orders import in bursts whenever
the user launches it.

**Periodic push reconciliation** is a different feature that the tray does *not* provide.
`listing_push` is event-driven with no retry, so a push that fails permanently — network
blip, expired token, marketplace 500 — is never retried until unrelated stock movement
happens to trigger one. The Phase 2 menu-bar badge now makes that visible (it counts
listings whose latest push attempt failed), which arguably makes the fix more urgent, not
less: the app now points at a problem it has no way to resolve on its own.

**Recommendation:** keep them separate. Ship the tray first, because it is self-contained.
Treat reconciliation as its own change — a periodic sweep that re-pushes listings whose
latest `PlatformListingPush` errored, bounded by the same semaphore, with a backoff so a
persistently broken listing isn't retried every cycle. It would benefit from the tray but
does not depend on it, and it would be a mistake to hide a marketplace-write behaviour
change inside a "keep the app running" feature.

---

## 5. Smaller decisions

- **Tray menu**: Open / Sync now / Quit. "Sync now" should call the existing
  `POST /platforms/{platform}/sync-orders`, which already takes `sync_scheduler`'s lock, so
  it can't race a background tick.
- **First close is surprising.** A window that vanishes instead of quitting confuses people.
  Show a one-time notification ("StockSmith is still running in the tray") on the first
  close after the feature is enabled.
- **Sleep/resume**: the scheduler's `asyncio.sleep(interval * 60)` simply resumes late after
  a suspend. Acceptable — the next tick catches up via the watermark. Not worth special
  handling.
- **Port 8000 stays occupied** while resident, so `uv run uvicorn` for dev work will fail to
  bind until the tray app quits. Mildly annoying; the §2a version check makes the failure
  legible rather than silent.

---

## 6. Uptime on this machine

The host is a **Windows 11 Dell OptiPlex that is also used for other things**. That is not
a server, and the difference matters: people sign out of it, it gets restarted, it sleeps,
and someone may deliberately quit StockSmith. The tray fixes one of those.

### 6a. What actually stops sync here

| # | Event | How often, realistically | Tray + autostart | + watchdog task (§6c) |
|---|---|---|---|---|
| 1 | Window closed with the X | Daily | **Fixed** | — |
| 2 | User signs out, or fast-user-switches | Weekly on a shared PC | **No** — the session ends and every process in it dies | **Fixed** |
| 3 | Windows Update restarts overnight | Monthly, unattended | **Only if ARSO is on** (§8) | **Fixed** |
| 4 | Shutdown, power cut, or any restart nobody signs back in from | Weekly | **No** — dead until someone logs on | **Fixed** |
| 5 | Machine sleeps | Nightly, unless configured otherwise | **No** — asleep is offline | **No** — host setting (§8) |
| 6 | Sidecar crashes on its own | Rare | **No** — the shell spawns it once and never looks again | Fixed, slowly (next sweep) |
| 7 | An app update half-applies | Every release | Asks a human (0.6.3) | Needs the rule in §6d |
| 8 | Dev backend already on port 8000 | Occasional | Legible, via the version check | Must not fight over it (§6d) |
| 9 | Someone quits from the tray on purpose | Occasional | Correct — respect it | **Must not resurrect it** (§6d) |

Rows 2 and 4 are the answer to "how do we survive an auto-update reboot": **the usual
autostart mechanism does not, and not for the reason people expect.** `HKCU\...\Run` is a
*logon* trigger. After an update restart, the OptiPlex sits at the lock screen with no
interactive session — nothing in `Run` has fired, and sync is down until somebody walks
over and signs in.

Windows has one narrow exception, and it is worth taking: **ARSO** (Winlogon Automatic
Restart Sign-On) signs the last active user back in automatically after an *update-initiated*
restart and immediately locks the screen, specifically so that user-context work can
finish. That makes `Run` entries fire, which covers row 3 — but **only** for restarts
Windows Update initiates. A power cut, a manual shutdown, or "restart now" from anyone
else leaves row 4 exactly as it was.

### 6b. Two tiers, and the honest gap between them

**Tier 1 — tray + autostart + ARSO.** Covers rows 1, 3 and 6 (once §6c's supervision is in
the shell). Zero new components, no stored credentials, nothing to explain. **Recommended
first, and possibly sufficient** — if this machine is normally left signed in, Tier 1 is
most of the uptime available.

**Tier 2 — a watchdog scheduled task.** Adds rows 2 and 4. Costs a stored credential, a
component that runs with no UI, and the ownership question that made the original spike
reject scheduled tasks outright (§1, "Rejected"). That rejection was aimed at a task that
*owns* the backend permanently; §6c is deliberately a narrower thing.

**Do not build Tier 2 blind.** §7 makes the gap measurable. Run Tier 1 for a few weeks,
look at how much downtime rows 2 and 4 actually account for on this specific machine, and
build Tier 2 only if the number justifies it.

### 6c. The watchdog task (Tier 2)

A Scheduled Task that **heals but never owns**:

- **Trigger:** at system startup, plus repeat every 15 minutes. Startup covers the reboot;
  the repeat covers a crash and a sign-out.
- **Security:** run as the ordinary user account, **"run whether user is logged on or
  not"**. That is what makes it independent of a session.
- **Action:** a small supervisor that probes `GET /healthz` and then does exactly one of
  three things — *answers and matches the installed version* → exit; *nothing answers* →
  start the sidecar headless; *answers with a different version* → log and exit (§6d).
- **Belt and braces:** the task's own "restart on failure" settings, which cost nothing.
- **No GUI, ever.** A task that runs without a session lives in session 0 and cannot show
  one. It does not need to — the thing that needs to stay alive is the Python sidecar, not
  the window. This is the same reframing the top of this document starts from.

Two things must be verified empirically rather than assumed, because both are classic
places this design breaks:

1. **That `%LOCALAPPDATA%` resolves to the right profile.** The sidecar's whole
   configuration model — `config.json`, the SQLite database, the asset root — hangs off it
   (`app/bootstrap.py`). User-specific paths failing in a non-interactive task is a
   well-known trap, and if it resolves elsewhere the task quietly creates a *second, empty*
   StockSmith database. That failure mode is bad enough to test first, before anything else
   in Tier 2 is built.
2. **That registering the task is possible without an admin prompt every time.** "Run
   whether user is logged on or not" stores a credential and needs the batch-logon right.
   If it turns out to need elevation, that changes the Settings toggle into a documented
   one-time setup step, which is acceptable — but it should be known before it is designed
   as a toggle.

### 6d. One ownership rule, and what it decides

> **The sidecar belongs to whoever started it, recorded in `backend.pid`. Nothing ever
> kills a healthy backend whose version matches the installed build.**

Everything awkward falls out of that:

- **Shell starts, backend already running and matching** → adopt it (today's behaviour).
- **Watchdog fires, backend healthy** → do nothing. It never competes with the shell.
- **Row 9, someone quits from the tray** → Quit kills the sidecar *and* writes a
  "stay down" marker; the watchdog honours it until the next boot or logon. A background
  process that comes back after you deliberately closed it is the behaviour people
  uninstall software over.
- **Row 8, a dev backend on 8000** → version mismatch, so the watchdog logs and leaves it
  alone rather than killing someone's debugging session.
- **Row 7, a half-applied update, unattended** → this is the interesting one, and it
  reverses the instinct. When the shell's version and the sidecar's disagree with nobody
  present, the *old sidecar is still syncing correctly*. Restarting it changes nothing
  (the on-disk backend binary is the one that failed to update — that is what the 0.6.3
  fix was about), so flapping it just adds downtime to a broken update. **Unattended:
  prefer availability.** Keep the working backend, record the mismatch loudly and
  persistently, and let the existing interactive "close it fully and re-run the installer"
  flow handle repair when a human is actually there.

### 6e. Explicitly not doing

- **Auto-logon** (`AutoAdminLogon` with a stored password). It would cover row 4, and on a
  shared machine it is a bad trade — it weakens sign-in for everything else the OptiPlex
  is used for, in exchange for one app's uptime.
- **A Windows Service.** Still rejected for §1's original reason: the whole configuration
  model is per-user.
- **Fighting the machine's sleep settings from code.** Keeping a shared desktop awake is
  the owner's decision, not the app's. §8 documents it instead.
- **Resurrecting after a deliberate Quit.** See §6d.

## 7. Knowing whether any of this worked

Uptime that isn't measured is a belief. Today, a night with no sync looks exactly like a
night with no orders.

**Derive it first, add schema only if that fails.** `platform_sync_runs` already gets a row
per tick per platform, so a gap wider than two intervals *is* downtime, and it is already
in the database. That supports a plain line in Settings → Integrations — "last 7 days: 3
gaps, 9h 20m total, longest 06:10-12:45" — with no migration at all.

Where that reading goes ambiguous is when auto-sync is off or a platform is disconnected,
because then there are no ticks for an innocent reason. If that ambiguity turns out to
matter in practice, *then* add an explicit heartbeat the scheduler stamps regardless of
platform state. Not before.

The tray tooltip should carry the same fact ("last synced 14:05"), since the window may be
hidden for days and a badge nobody can see is not doing much (Open question 3).

## 8. Host setup this needs (documented, not automated)

These are the machine's settings, not the app's, and the app should not silently change
them. They belong in the README once the tray ships:

- **ARSO on** — Settings → Accounts → Sign-in options → "Use my sign-in info to
  automatically finish setting up after an update". This is what makes row 3 work. It is
  per-user and it is not on by default in every configuration.
- **Sleep** — on a mains-powered desktop, "never" is the setting that matches the goal.
  Screen-off is unrelated and can stay. If sleep is wanted, wake timers must be enabled or
  row 5 stands.
- **Windows Update active hours** — so restarts land at a predictable time rather than
  mid-afternoon.
- **Fast Startup** — worth checking rather than assuming: a Windows "shutdown" with Fast
  Startup on is a hybrid hibernate, not a cold boot, and whether "at system startup" task
  triggers fire on that path needs confirming on this machine before Tier 2 is trusted.
- **Antivirus / SmartScreen** — the installer is unsigned (README) and the sidecar is an
  unsigned PyInstaller binary that opens a listening socket. If anything quarantines it,
  every tier above fails at once. Worth an exclusion, and worth checking first when the app
  is mysteriously dead.
- **Don't run a dev backend on 8000 on this box** — it doesn't break anything now that the
  version check exists, but it does mean the packaged app refuses to start.

## Open questions

0. **Is Tier 1 enough?** Answer it with §7's data rather than in advance — how often is
   this machine actually signed out or off, versus merely having the app closed? Everything
   in §6c hangs on that number, and building Tier 2 before knowing it is building on a
   guess.
1. **Should closing the window hide, or should hiding be opt-in too?** Hiding on close is the
   conventional tray behaviour but is the single most surprising part. The alternative is a
   "keep running in the background when closed" setting that gates both the hide and the
   tray icon, so users who never enable it see today's behaviour exactly.
2. **Is autostart actually wanted, or just "don't die when I close the window"?** The backlog
   entry asks for boot-start explicitly, but the sync gap it describes is mostly solved by
   surviving a window close. Autostart is the part that carries the update and
   single-instance complications.
3. **Does the tray need a failure notification**, or is the Phase 2 menu-bar badge enough?
   A badge nobody sees because the window is hidden is not doing much.

---

## Suggested build order

**Tier 1 — built, unreleased. Every item below is code-complete; what none of it has had is
an installed build on the OptiPlex, which is where the verification pass comes in.**

1. ✅ **PID-file reaping** (§2b). `backend.pid` written on spawn, and a recorded process
   that is still alive is killed before spawning — but only once the port has been found
   silent, so a healthy backend is never what gets reaped. Adopting one of our own orphans
   now also takes ownership of it, which is what stops it outliving the shell a second time.
2. ✅ **Single-instance plugin** (§2c), registered before every other plugin. A second
   launch surfaces the existing window rather than doing nothing, which matters most when
   the first one is hidden and therefore indistinguishable from not running.
3. ✅ **Tray icon, hide-on-close, real Quit** (§1). Close hides; the tray menu is Open and
   Quit; Quit confirms, writes the §6d stay-down marker and exits. The one-time "still
   running" notice is in (§5). Handled in Rust rather than the frontend so a wedged webview
   can't produce an X that does nothing.
4. ✅ **Sidecar supervision in the shell** — probes every 30s, tolerates one missed probe,
   restarts with a 30s→5min backoff, and only ever supervises a backend this shell owns so
   it can't fight a developer restarting their own.
5. ✅ **Autostart toggle in Settings** (§3). Reads the registry live and reports back what
   Windows actually kept, so plugins-workspace#771 shows up as a warning rather than a
   silent lie. **The two-reboot check itself is still outstanding** — it needs an install.
6. ✅ **Sync-health visibility** (§7), derived from `platform_sync_runs` with no new schema,
   surfaced as "sync coverage, last 7 days" beside the toggle.

**Deliberately not built:** "Sync now" in the tray menu (§5). It needs the shared password
in the Rust shell, which today only the frontend holds — a real design question for a menu
item that saves one click over opening the window, so it waits rather than being rushed in
beside work that doesn't depend on it.

**Then stop and look at the data.**

**Tier 2 — only if §7 says rows 2 and 4 matter here.**

7. **Watchdog scheduled task** (§6c, §6d), starting with the `%LOCALAPPDATA%` resolution
   test — if that fails, the whole tier is redesigned or dropped, so it goes first.

**Separate, unchanged.**

8. **Push reconciliation** (§4) — its own change, its own decision, as argued above.

Steps 1 and 2 are worth landing even if the tray work is dropped.

---

## Verification

Steps 1-2 are unit-testable in the existing suite: `/healthz` shape, and a Rust test for
the version-mismatch decision in `spawn_sidecar_if_needed`.

Everything after that needs an **installed build** — none of it can be exercised by
`npm run tauri dev`, since the failure modes are all about installers, boot, and multiple
instances. The manual pass, in order:

1. Install, enable autostart, reboot **twice**. Confirm each time: no window appears, tray
   icon present, `GET /healthz` answers — and after the first reboot, that the `Run`
   registry entry still exists and the Settings toggle still reads enabled
   (plugins-workspace#771, §3).
2. With the app resident, close the window. Confirm the process survives and a sync tick
   still runs (check `platform_sync_runs` for a new row after the interval).
3. Launch from the Start menu while resident. Confirm the existing window is shown and
   focused, and that exactly one `stocksmith-backend.exe` is running.
4. **The one that matters**: with the app resident and autostarted, publish an update and
   accept it. Confirm afterwards that only one backend is running and that its version
   matches the installed app — this is §2a, and it is the scenario most likely to be broken.
5. Kill `StockSmith.exe` from Task Manager. Confirm the next launch reaps the orphaned
   backend rather than adopting it.
6. Quit from the tray. Confirm no `stocksmith-backend.exe` remains.

**Uptime pass (§6), on the OptiPlex itself — these are the ones the goal actually rests
on.** Each is "leave it, come back, check `platform_sync_runs` for ticks across the gap":

7. **Sign out** (don't restart). Tier 1: expect sync to stop — confirm it, so the gap in
   §7's reading is understood rather than mysterious. Tier 2: expect ticks to continue.
8. **Let a real Windows Update restart happen overnight** with ARSO on (§8). Expect the
   machine to sign back in, lock, and sync to resume without anyone touching it. This is
   the scenario the whole tier exists for, and it cannot be faked convincingly — a manual
   "Restart" is a *different* path that ARSO does not cover.
9. **Full shutdown, then power on and walk away** without signing in. Tier 1: nothing runs
   (expected). Tier 2: sync resumes at the lock screen.
10. **Kill `stocksmith-backend.exe`** while the app is running. Expect the shell to restart
    it within the backoff, and expect exactly one process afterwards.
11. **Tier 2 only, and first:** run the watchdog task once with nobody signed in, then
    confirm it used `%LOCALAPPDATA%\StockSmith\stocksmith.db` — the real database, with the
    real data in it — and did not create a second one elsewhere. If this fails, stop; the
    design is wrong, not the configuration.
12. **Tier 2 only:** Quit from the tray, wait out a watchdog interval, confirm it stays
    down (§6d, row 9), then reboot and confirm it comes back.
