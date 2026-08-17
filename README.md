# StockSmith

Windows desktop inventory & BOM tracker for a small maker business (3D printing/resin products).

- `backend/` — FastAPI + SQLite API, bundled into the desktop app as a sidecar process.
- `frontend/` — Tauri (React + TypeScript) desktop client.

See [docs/plan-phase0-phase1.md](docs/plan-phase0-phase1.md) for the original build plan, and
[docs/roadmap.md](docs/roadmap.md) for where things stand now and what's planned to 1.0.

## Running the app

Double-click the StockSmith installer, install it, then launch StockSmith like any other
desktop app. No Docker, no separate database server, no manual backend startup — the app
launches its own bundled backend automatically and shows its window once it's ready.

**First launch trigger a Windows SmartScreen warning** ("Windows protected your PC") since
the installer isn't code-signed. Click "More info" → "Run anyway" — this is a one-time
step, not a sign anything's wrong.

The app checks for updates automatically on startup. If a newer published release exists,
it'll ask before downloading/installing (installing always restarts the app). No update
check happens if there's no internet connection or no release has been published yet —
that's not an error, it just means you're already on the latest version.

### Keeping it syncing on an always-on PC

Closing the window leaves StockSmith running in the notification area, and Settings →
General → Background syncing can start it when you sign in. That covers the app being
closed. It does **not**, on its own, cover the PC restarting — and a few Windows settings
decide whether it does. None of these are changed by StockSmith; they're yours to set.

- **Sign back in automatically after an update.** Settings → Accounts → Sign-in options →
  "Use my sign-in info to automatically finish setting up after an update". Autostart runs
  when you sign in, so without this an overnight Windows Update restart leaves the PC at the
  lock screen and StockSmith not running until somebody signs in. With it, Windows signs you
  back in, locks the screen, and StockSmith starts. Note it applies to update restarts only —
  a power cut or a manual shutdown still needs a real sign-in.
- **Sleep.** A sleeping PC isn't syncing. On a mains-powered desktop, Settings → System →
  Power → Screen and sleep → "make my device sleep after" = Never is what matches the goal.
  Turning the screen off is unrelated and fine to keep.
- **Windows Update active hours**, so restarts land overnight rather than mid-afternoon.
- **Antivirus.** The installer isn't signed and the backend is an unsigned executable that
  opens a local port. If something quarantines it, StockSmith stops working entirely — worth
  an exclusion for `%LOCALAPPDATA%\StockSmith\`, and worth checking first if the app is
  mysteriously dead.

Settings → General → Background syncing lists any stretches in the last week where nothing
synced, which is how to check whether any of the above is actually working.

### Where your data lives

Everything the app stores lives under `%LOCALAPPDATA%\StockSmith\`:

- `data\stocksmith.db` — the SQLite database (materials, products, orders, everything).
- `assets\` — uploaded product/material images and files.
- `config.json` — the auto-generated connection password and encryption key for this install.
- `backend.log` — backend startup/error log, useful if the app fails to start.

Back this folder up if you want to preserve your data; delete it to reset the app to a
fresh, empty state (a new one is created automatically on the next launch).

### Known limitation: Etsy/eBay platform integrations

The Etsy/eBay OAuth connection flow currently assumes the backend is reachable at a
stable, externally-resolvable URL (it was originally designed for a Tailscale-based
networked setup). In the packaged local-only app, the backend only listens on
`127.0.0.1`, and whether Etsy/eBay's OAuth apps accept a loopback redirect URI needs to
be verified against each platform's actual developer-console constraints. This is tracked
as a follow-up, not a blocker for the base desktop app — inventory, products, orders, and
manual builds all work independent of any platform connection.

## Development against Postgres (optional)

The app defaults to SQLite for local development too (`backend/.env`'s
`DATABASE_URL=sqlite+aiosqlite:///./dev.db`) — no Docker needed. If you want to exercise
the Postgres path instead (e.g. to double-check a schema change is still dialect-portable),
`docker-compose.yml` still spins up a Postgres container; point `DATABASE_URL` in
`backend/.env` at it and run `alembic upgrade head`.

`scripts/dev/` has the old Docker/Postgres dev-loop launchers, kept for that optional path:

- `start-backend.bat` / `start-backend.ps1` — brings up Docker Desktop, the Postgres
  container, and the API server in the foreground.
- `start-stocksmith.bat` / `start-stocksmith.ps1` — same, plus opens the Tauri dev app
  window; leaves the API server running in the background afterward so a later launch
  can reuse it instead of re-bringing-up Docker each time.
- `stop-backend-service.bat` / `stop-backend-service.ps1` — stops that backgrounded API
  server.

### Optional: auto-start the dev backend at login

To have the dev backend start automatically whenever you log in to Windows, register it
as a Scheduled Task. This changes Windows startup behavior, so it's not done
automatically — run this yourself in an elevated PowerShell prompt when you're ready:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\Projects\StockSmith\scripts\dev\start-backend.ps1"'
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "StockSmith Backend" -Action $action -Trigger $trigger -Description "Starts the StockSmith dev backend (Docker + Postgres + API) at login"
```

To remove it later: `Unregister-ScheduledTask -TaskName "StockSmith Backend"`.

## Building the installer locally

```bash
powershell -File backend/build.ps1   # packages the backend into frontend/src-tauri/binaries/
cd frontend && npm run tauri build   # produces the Windows installer
```

## Releasing a new version

Releases are built and signed by GitHub Actions (`.github/workflows/release.yml`) and
published to [GitHub Releases](https://github.com/TheManoeuvre/StockSmith-App/releases) —
that's also where the installed app's auto-updater looks for new versions.

To ship a new version:

1. Move the `## [Unreleased]` items in `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD`
   heading. **These notes are what users see** — the release workflow extracts this
   section into the GitHub Release body and `latest.json`, and the in-app update prompt
   displays it when asking whether to install. Write it for them, not for the commit log.
2. Bump `version` in **both** `frontend/src-tauri/tauri.conf.json` and
   `frontend/package.json` (keep them in sync) and commit.
3. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. Wait for the [Release workflow](https://github.com/TheManoeuvre/StockSmith-App/actions)
   to finish — it builds the backend sidecar, builds and signs the Tauri app, and creates
   a **draft** GitHub Release with the installer, `latest.json`, and signature attached.
5. Review the draft release — the body should already hold this version's changelog
   section — then click **Publish release**.
6. Any already-installed copy of StockSmith will offer this update the next time it's
   launched, showing those notes in the prompt.

If the tag has no matching `CHANGELOG.md` section the build still succeeds, but the
release body falls back to a generic line — the workflow log will carry a warning.

### One-time setup (already done for this repo)

The updater requires a signing keypair: the public key lives in `tauri.conf.json`
(`plugins.updater.pubkey`), and the private key is stored as the `TAURI_SIGNING_PRIVATE_KEY`
GitHub Actions secret (with `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` alongside it) — never
committed to the repo. If this keypair is ever lost, existing installs can no longer
receive signed updates and a new keypair (and a fresh non-updating release for users to
manually reinstall) would be needed.
