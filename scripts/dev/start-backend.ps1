#Requires -Version 5.1
<#
.SYNOPSIS
  Only needed for the optional "develop against real Postgres" path — the packaged
  desktop app is fully self-contained (SQLite, no Docker) and doesn't use this at all.
  Brings up StockSmith's backend from a cold start: Docker Desktop, then Postgres container, then API server.
  Run this by double-clicking start-backend.bat, or directly with powershell -File start-backend.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$BackendDir = Join-Path $ProjectRoot "backend"

function Test-DockerRunning {
    try {
        docker info *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

Write-Host "== StockSmith backend launcher ==" -ForegroundColor Cyan

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

Write-Host "Starting the API server (uv run uvicorn app.main:app)..." -ForegroundColor Cyan
Push-Location $BackendDir
try {
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
