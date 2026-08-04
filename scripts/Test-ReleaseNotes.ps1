<#
.SYNOPSIS
    Tests scripts/Get-ReleaseNotes.ps1.

.DESCRIPTION
    Plain assertions rather than Pester, so this runs anywhere PowerShell does with nothing
    to install — it exists to be cheap enough that CI always runs it.

    Exercises fixtures rather than only the real CHANGELOG.md, so the cases that matter
    (missing version, empty section, a version whose dots could act as wildcards) stay
    testable no matter what the real file happens to contain. One case does read the real
    file, to catch the changelog drifting into a shape the pattern no longer matches.

.EXAMPLE
    ./scripts/Test-ReleaseNotes.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Failures = 0
$extract = Join-Path $PSScriptRoot 'Get-ReleaseNotes.ps1'

function Assert-Equal {
    param([string] $Name, $Expected, $Actual)
    if ($Expected -eq $Actual) {
        Write-Host "  PASS  $Name"
    } else {
        Write-Host "  FAIL  $Name"
        Write-Host "        expected: $Expected"
        Write-Host "        actual:   $Actual"
        $script:Failures++
    }
}

function Assert-Match {
    param([string] $Name, [string] $Pattern, [string] $Actual)
    if ($Actual -match $Pattern) {
        Write-Host "  PASS  $Name"
    } else {
        Write-Host "  FAIL  $Name"
        Write-Host "        expected to match: $Pattern"
        Write-Host "        actual:            $Actual"
        $script:Failures++
    }
}

$fixture = Join-Path ([System.IO.Path]::GetTempPath()) "changelog-fixture-$(New-Guid).md"
@'
# Changelog

## [Unreleased]

### Added
- Something not yet shipped.

## [1.2.0] - 2026-08-04

### Added
- A headline feature.

### Fixed
- A bug worth mentioning.

## [1.1.0] - 2026-08-01

### Fixed
- An older fix.

## [1.0.0] - 2026-07-01
'@ | Set-Content -Path $fixture -Encoding UTF8

try {
    Write-Host 'Get-ReleaseNotes'

    $notes = & $extract -Version '1.2.0' -ChangelogPath $fixture
    Assert-Match 'extracts the requested section' 'A headline feature' $notes
    # The section must stop at the next heading, or every release would carry the whole
    # history of the file below it.
    Assert-Match 'stops before the next version' '^(?!.*An older fix)(?s).*$' $notes
    Assert-Match 'keeps subsection headings' '### Fixed' $notes

    $prefixed = & $extract -Version 'v1.2.0' -ChangelogPath $fixture
    Assert-Equal 'accepts a v-prefixed tag name' $notes $prefixed

    $older = & $extract -Version '1.1.0' -ChangelogPath $fixture
    Assert-Match 'extracts a non-latest section' 'An older fix' $older

    # A version's dots are regex wildcards if unescaped, so "1X2X0" would match too.
    $wildcard = & $extract -Version '1X2X0' -ChangelogPath $fixture -WarningAction SilentlyContinue
    Assert-Match 'does not treat dots as wildcards' 'commit history' $wildcard

    $missing = & $extract -Version '9.9.9' -ChangelogPath $fixture -WarningAction SilentlyContinue
    Assert-Match 'falls back for an unknown version' 'commit history' $missing

    # A heading with nothing under it must not publish a blank release body.
    $empty = & $extract -Version '1.0.0' -ChangelogPath $fixture -WarningAction SilentlyContinue
    Assert-Match 'falls back for an empty section' 'commit history' $empty

    $noFile = & $extract -Version '1.2.0' -ChangelogPath 'does-not-exist.md' -WarningAction SilentlyContinue
    Assert-Match 'falls back when the changelog is missing' 'commit history' $noFile

    # Guards against the real changelog drifting into a shape the pattern can't read.
    # Deliberately a shipped version rather than [Unreleased], which is legitimately empty
    # immediately after a release is cut — asserting content there would fail every time.
    $realChangelog = Join-Path (Join-Path $PSScriptRoot '..') 'CHANGELOG.md'
    $shipped = & $extract -Version '0.4.2' -ChangelogPath $realChangelog
    Assert-Match 'reads a shipped section from the real CHANGELOG.md' '###' $shipped

    # Exercises the default -ChangelogPath, which nothing above reaches because they all
    # pass it explicitly. That is exactly how a broken default shipped unnoticed.
    Push-Location (Join-Path $PSScriptRoot '..')
    try {
        $defaulted = & $extract -Version '0.4.2'
        Assert-Match 'resolves the default changelog path' '###' $defaulted
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item $fixture -ErrorAction SilentlyContinue
}

if ($script:Failures -gt 0) {
    Write-Host ''
    Write-Host "$script:Failures assertion(s) failed"
    exit 1
}

Write-Host ''
Write-Host 'All release-notes assertions passed'
