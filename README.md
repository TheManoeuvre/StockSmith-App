# StockSmith

Windows desktop inventory & BOM tracker for a small maker business (3D printing/resin products).

- `backend/` — FastAPI + Postgres API, runs on a "home base" PC, reachable from other devices via Tailscale.
- `frontend/` — Tauri (React + TypeScript) desktop client.

See [docs/plan-phase0-phase1.md](docs/plan-phase0-phase1.md) for the current build plan.

## Running the backend

Double-click `scripts/start-backend.bat` (or run `scripts/start-backend.ps1` directly in PowerShell). It brings Docker Desktop up if it isn't running, starts the Postgres container, waits for it to accept connections, then launches the API server in the foreground on port 8000. Leave the window open while you use the app; close it (or Ctrl+C) to stop the server.

### Optional: auto-start at login

To have the backend start automatically whenever you log in to Windows, register it as a Scheduled Task. This changes Windows startup behavior, so it's not done automatically — run this yourself in an elevated PowerShell prompt when you're ready:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\Projects\StockSmith\scripts\start-backend.ps1"'
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "StockSmith Backend" -Action $action -Trigger $trigger -Description "Starts the StockSmith backend (Docker + Postgres + API) at login"
```

To remove it later: `Unregister-ScheduledTask -TaskName "StockSmith Backend"`.
