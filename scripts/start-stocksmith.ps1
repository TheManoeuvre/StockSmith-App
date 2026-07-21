#Requires -Version 5.1
<#
.SYNOPSIS
  One-click launcher for StockSmith: brings up Docker/Postgres, the API server, and the
  Tauri desktop app window, verifying each layer is actually working before moving to the
  next. Run this by double-clicking start-stocksmith.bat, or directly with
  powershell -File start-stocksmith.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$BackendLog = Join-Path $ProjectRoot "backend.log"

function Test-DockerRunning {
    try {
        docker info *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-BackendHealthy {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Host "== StockSmith launcher ==" -ForegroundColor Cyan

# 1. Docker Desktop
if (-not (Test-DockerRunning)) {
    Write-Host "Docker Desktop is not running - starting it..." -ForegroundColor Yellow
    $dockerDesktopPaths = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Programs\Docker\Docker\Docker Desktop.exe"
    )
    $dockerExe = $dockerDesktopPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $dockerExe) {
        Write-Host "Could not find Docker Desktop. Please install it or start it manually, then re-run this script." -ForegroundColor Red
        exit 1
    }
    Start-Process -FilePath $dockerExe

    Write-Host "Waiting for Docker Desktop to become ready (this can take a minute)..."
    $waited = 0
    while (-not (Test-DockerRunning)) {
        Start-Sleep -Seconds 3
        $waited += 3
        if ($waited -ge 180) {
            Write-Host "Docker Desktop did not become ready within 3 minutes. Check it manually and re-run this script." -ForegroundColor Red
            exit 1
        }
    }
}
Write-Host "Docker is running." -ForegroundColor Green

# 2. Postgres container
Write-Host "Starting Postgres container (docker compose up -d)..."
Push-Location $ProjectRoot
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker compose up failed." -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

Write-Host "Waiting for Postgres to accept connections..."
$waited = 0
while ($true) {
    docker exec stocksmith-postgres pg_isready -U stocksmith *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
    $waited += 2
    if ($waited -ge 60) {
        Write-Host "Postgres did not become ready within 60 seconds." -ForegroundColor Red
        exit 1
    }
}
Write-Host "Postgres is ready." -ForegroundColor Green

# 3. API server (background, so this script can keep going and later open the app window)
# Left running after the app window closes (see step 6) so it keeps serving Etsy/eBay
# OAuth callbacks and sync independent of whether the desktop app is open — so a launch
# reuses an already-healthy instance from a previous run instead of trying (and failing)
# to bind port 8000 again.
if (Test-BackendHealthy) {
    Write-Host "API server is already running and healthy - reusing it." -ForegroundColor Green
} else {
    Write-Host "Starting the API server (uv run uvicorn app.main:app)..." -ForegroundColor Cyan
    $backendErrLog = Join-Path $ProjectRoot "backend.err.log"
    $backendProcess = Start-Process -FilePath "uv" `
        -ArgumentList "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $BackendLog `
        -RedirectStandardError $backendErrLog `
        -WindowStyle Hidden `
        -PassThru

    Write-Host "Waiting for the API to respond on /healthz..."
    $waited = 0
    $healthy = $false
    while ($waited -lt 30) {
        if (Test-BackendHealthy) { $healthy = $true; break }
        if ($backendProcess.HasExited) {
            Write-Host "The API server process exited unexpectedly. Check $BackendLog and $backendErrLog for details." -ForegroundColor Red
            exit 1
        }
        Start-Sleep -Seconds 1
        $waited += 1
    }
    if (-not $healthy) {
        Write-Host "The API did not become healthy within 30 seconds. Check $BackendLog and $backendErrLog for details." -ForegroundColor Red
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Host "API server is healthy." -ForegroundColor Green
}

# 4. Frontend dependencies
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "Frontend dependencies not installed - running npm install..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "npm install failed." -ForegroundColor Red
            if ($backendProcess) { Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue }
            exit 1
        }
    } finally {
        Pop-Location
    }
}

# 5. Open the app window (blocks until the window is closed)
Write-Host "Opening StockSmith..." -ForegroundColor Cyan
Push-Location $FrontendDir
try {
    npm run tauri dev
} finally {
    Pop-Location
}

# 6. The app window closed (or crashed) — the API server is deliberately left running in
# the background. It's stateless/cheap to leave up, it serves Etsy/eBay OAuth callbacks
# and sync independent of whether the desktop app is open, and a future launch will just
# reuse it (see step 3) rather than fighting over port 8000. Run stop-backend-service.ps1
# if you actually want to stop it (e.g. before a reboot).
Write-Host "StockSmith window closed. The API server is still running in the background at http://127.0.0.1:8000." -ForegroundColor Cyan
Write-Host "To stop it, run scripts\stop-backend-service.ps1 (or stop-backend-service.bat)." -ForegroundColor Cyan
Write-Host "Done." -ForegroundColor Green
