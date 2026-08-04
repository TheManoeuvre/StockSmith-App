# StockSmith — Background / Tray Sync: Planning Pass

## Status

Planning only — nothing in this document has been implemented. Produced by the time-boxed
spike scheduled as Phase 5 of the backlog burndown, to answer the questions
`docs/backlog.md`'s "Background/tray process to keep stock sync alive" entry left open
before any code is written.

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

## Open questions

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

1. **`/healthz` identity + PID-file reaping** (§2a, §2b). Independent of everything else,
   fixes a latent bug that exists today, and is a prerequisite for the rest being safe.
2. **Single-instance plugin** (§2c). Small, and required before autostart.
3. **Tray icon, hide-on-close, real Quit** (§1). The core of the feature.
4. **Autostart toggle in Settings** (§3).
5. **Push reconciliation** (§4) — separate change, separate decision.

Steps 1 and 2 are worth landing even if the tray work is dropped.

---

## Verification

Steps 1-2 are unit-testable in the existing suite: `/healthz` shape, and a Rust test for
the version-mismatch decision in `spawn_sidecar_if_needed`.

Everything after that needs an **installed build** — none of it can be exercised by
`npm run tauri dev`, since the failure modes are all about installers, boot, and multiple
instances. The manual pass, in order:

1. Install, enable autostart, reboot. Confirm: no window appears, tray icon present, and
   `GET /healthz` answers.
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
