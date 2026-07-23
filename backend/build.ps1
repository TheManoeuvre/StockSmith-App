# Builds the packaged backend and drops it where Tauri's sidecar mechanism expects it.
#
# Usage: powershell -File build.ps1
#
# PyInstaller produces a single onefile exe (dist/stocksmith-backend.exe) — no companion
# folder to keep adjacent, which sidesteps a real placement mismatch: Tauri's
# `externalBin` convention copies only the one named file, and testing confirmed a
# onedir build's `resources`-bundled `_internal/` folder lands in a different directory
# than the sidecar exe, breaking PyInstaller's bootloader. Onefile just needs the one
# file copied to the target-triple-suffixed name Tauri's sidecar convention expects.

$ErrorActionPreference = "Stop"

$TargetTriple = (rustc -Vv | Select-String "^host: (.+)$").Matches.Groups[1].Value
if (-not $TargetTriple) {
    throw "Could not determine Rust target triple (is rustc on PATH?)"
}

$BackendDir = $PSScriptRoot
$DistExe = Join-Path $BackendDir "dist\stocksmith-backend.exe"
$BinariesDir = Join-Path $BackendDir "..\frontend\src-tauri\binaries"

Write-Host "Building backend with PyInstaller..."
Push-Location $BackendDir
try {
    uv run pyinstaller stocksmith-backend.spec --noconfirm
} finally {
    Pop-Location
}

if (-not (Test-Path $DistExe)) {
    throw "PyInstaller output not found at $DistExe"
}

New-Item -ItemType Directory -Force -Path $BinariesDir | Out-Null

$SuffixedExeName = "stocksmith-backend-$TargetTriple.exe"
Copy-Item -Force $DistExe (Join-Path $BinariesDir $SuffixedExeName)

Write-Host "Done. Sidecar binary: $BinariesDir\$SuffixedExeName"
