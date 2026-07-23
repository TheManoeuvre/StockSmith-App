#Requires -Version 5.1
<#
.SYNOPSIS
  Only needed for the optional "develop against real Postgres" path — the packaged
  desktop app manages its own backend process lifecycle and doesn't need this at all.
  Stops the StockSmith API server left running in the background by start-stocksmith.ps1.
  The normal dev launcher no longer stops the API server when the desktop window closes
  (so it keeps serving Etsy/eBay OAuth callbacks and sync in the background) — run this
  when you actually want to shut it down, e.g. before restarting the machine.
  Run this by double-clicking stop-backend-service.bat, or directly with
  powershell -File stop-backend-service.ps1
#>

$connection = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $connection) {
    Write-Host "No process is listening on port 8000 - the API server isn't running." -ForegroundColor Yellow
    exit 0
}

$proc = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "Could not resolve the process listening on port 8000 (PID $($connection.OwningProcess))." -ForegroundColor Red
    exit 1
}

Write-Host "Stopping API server (PID $($proc.Id), $($proc.ProcessName))..." -ForegroundColor Cyan
Stop-Process -Id $proc.Id -Force
Write-Host "Stopped." -ForegroundColor Green
