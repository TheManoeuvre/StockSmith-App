<#
.SYNOPSIS
    Extracts one version's section from CHANGELOG.md, for use as a GitHub Release body.

.DESCRIPTION
    Lives in a script rather than inline in release.yml so it can be tested: that workflow
    only triggers on v* tags, so anything embedded in it is unexercised until the moment it
    matters. See scripts/Test-ReleaseNotes.ps1.

    The extracted text becomes the release body, which tauri-action also writes into
    latest.json's `notes` field, which is what the in-app update prompt displays.

    Fail-soft by design: a version with no matching section emits a warning and returns
    generic text rather than failing. A release with thin notes beats a release that didn't
    build, and the draft is reviewed by hand before publishing anyway.

.PARAMETER Version
    Version to extract, with or without a leading "v" (both "v0.4.2" and "0.4.2" work).

.PARAMETER ChangelogPath
    Path to the changelog. Defaults to CHANGELOG.md at the repo root.

.EXAMPLE
    ./scripts/Get-ReleaseNotes.ps1 -Version v0.4.2
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Version,

    [string] $ChangelogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Resolved here rather than as a param default: under Windows PowerShell 5.1 $PSScriptRoot
# is empty while param defaults are being bound, so the default silently produced a broken
# path. Nested Join-Path rather than the 3-argument form, which is PowerShell 7+ only.
if (-not $ChangelogPath) {
    $ChangelogPath = Join-Path (Join-Path $PSScriptRoot '..') 'CHANGELOG.md'
}

$fallback = 'See the commit history for changes in this release.'

if (-not (Test-Path $ChangelogPath)) {
    Write-Warning "No changelog found at $ChangelogPath"
    return $fallback
}

$number = $Version -replace '^v', ''
$content = Get-Content $ChangelogPath -Raw -Encoding UTF8

# Matches from this version's "## [x.y.z]" heading to the next "## " heading (or end of
# file). Multiline + singleline so ^ anchors to line starts and . spans the section body.
# The version is regex-escaped because the dots in a version number are otherwise wildcards
# — without it, "0.4.2" would also match a "0X4X2" heading.
$pattern = "(?ms)^## \[$([regex]::Escape($number))\].*?\r?\n(.*?)(?=^## |\z)"
$match = [regex]::Match($content, $pattern)

if (-not $match.Success) {
    Write-Warning "No CHANGELOG.md section found for version $number"
    return $fallback
}

$notes = $match.Groups[1].Value.Trim()
if ([string]::IsNullOrWhiteSpace($notes)) {
    # A heading with nothing under it — treat as missing rather than publishing a blank body.
    Write-Warning "CHANGELOG.md section for version $number is empty"
    return $fallback
}

return $notes
