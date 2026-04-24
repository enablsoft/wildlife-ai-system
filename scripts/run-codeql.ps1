<#
.SYNOPSIS
  Run local CodeQL analysis for this repository.

.DESCRIPTION
  Creates (or reuses) a local CodeQL database and runs Python security+quality
  queries, outputting SARIF for review in editors or upload to GitHub.

.EXAMPLE
  .\scripts\run-codeql.ps1

.EXAMPLE
  .\scripts\run-codeql.ps1 -DatabasePath ".codeql-db" -OutputSarif "codeql-results.sarif" -OverwriteDb
#>

[CmdletBinding()]
param(
    [string]$DatabasePath = ".codeql-db",
    [string]$OutputSarif = "codeql-results.sarif",
    [switch]$OverwriteDb,
    [switch]$SkipCreate
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Resolve-CodeqlExe {
    if (Get-Command codeql -ErrorAction SilentlyContinue) {
        return "codeql"
    }

    $candidate = Join-Path $env:USERPROFILE "tools\codeql\codeql\codeql.exe"
    if (Test-Path $candidate) {
        return $candidate
    }

    throw "CodeQL CLI not found. Install it first, then re-run this script."
}

$codeqlExe = Resolve-CodeqlExe
$querySuite = "codeql/python-queries:codeql-suites/python-security-and-quality.qls"
$dbFull = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $DatabasePath))
$sarifFull = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputSarif))

if (-not $SkipCreate) {
    $createArgs = @("database", "create", $dbFull, "--language=python", "--source-root", ".")
    if ($OverwriteDb) {
        $createArgs += "--overwrite"
    }
    Write-Host "Creating CodeQL database: $dbFull"
    & $codeqlExe @createArgs
}

Write-Host "Ensuring Python query pack is installed..."
& $codeqlExe pack download codeql/python-queries

Write-Host "Running CodeQL analysis..."
& $codeqlExe database analyze $dbFull $querySuite "--format=sarifv2.1.0" "--output=$sarifFull"

Write-Host "CodeQL analysis complete."
Write-Host "SARIF output: $sarifFull"
