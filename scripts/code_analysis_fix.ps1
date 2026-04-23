<#
.SYNOPSIS
  Code analysis smoke test for GitHub alerts and local checks.

.DESCRIPTION
  - Fetches open GitHub CodeQL alerts via gh CLI
  - Optionally applies known local fixes for recurring CodeQL patterns
  - Runs critical flake8 checks and targeted pytest smoke tests

.EXAMPLE
  .\scripts\code_analysis_fix.ps1

.EXAMPLE
  .\scripts\code_analysis_fix.ps1 -ApplyKnownFixes
#>

[CmdletBinding()]
param(
    [string]$Repo = "",
    [switch]$ApplyKnownFixes,
    [switch]$SkipLocalChecks
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "Preflight: checking remote and local repo status..."
& "$PSScriptRoot\check-repo-state.ps1"

$argsList = @("scripts/code_analysis_fix.py")
if ($Repo) { $argsList += @("--repo", $Repo) }
if ($ApplyKnownFixes) { $argsList += "--apply-known-fixes" }
if ($SkipLocalChecks) { $argsList += "--skip-local-checks" }

python @argsList
exit $LASTEXITCODE
