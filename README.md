# StockSmith

Windows desktop inventory & BOM tracker for a small maker business (3D printing/resin products).

- `backend/` — FastAPI + SQLite API, bundled into the desktop app as a sidecar process.
- `frontend/` — Tauri (React + TypeScript) desktop client.

See [docs/plan-phase0-phase1.md](docs/plan-phase0-phase1.md) for the original build plan.

## Running the app

Double-click the StockSmith installer, install it, then launch StockSmith like any other
desktop app. No Docker, no separate database server, no manual backend startup — the app
launches its own bundled backend automatically and shows its window once it's ready.

**First launch trigger a Windows SmartScreen warning** ("Windows protected your PC") since
the installer isn't code-signed. Click "More info" → "Run anyway" — this is a one-time
step, not a sign anything's wrong.

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

## Building the installer

```bash
powershell -File backend/build.ps1   # packages the backend into frontend/src-tauri/binaries/
cd frontend && npm run tauri build   # produces the Windows installer
```
