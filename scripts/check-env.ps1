<#
.SYNOPSIS
  Validate required .env keys for app and backend scripts.

.DESCRIPTION
  Reads key/value pairs from `.env` (or another file) and reports missing keys.
  Exits non-zero when required keys are missing.

.PARAMETER EnvFile
  Path to env file (default: .env in repo root).

.PARAMETER ForMongo
  Also require Mongo-specific keys.
#>

param(
    [string]$EnvFile = ".env",
    [switch]$ForMongo
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$resolvedEnv = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $repoRoot $EnvFile }
if (-not (Test-Path $resolvedEnv)) {
    Write-Error "Env file not found: $resolvedEnv"
}

function Parse-EnvFile {
    param([string]$Path)
    $map = @{}
    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $trimmed = ($line -as [string]).Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch "=") { continue }
        $parts = $trimmed -split "=", 2
        $k = $parts[0].Trim()
        $v = $parts[1].Trim().Trim("'`"")
        if ($k) {
            $map[$k] = $v
        }
    }
    return $map
}

$values = Parse-EnvFile -Path $resolvedEnv

$required = @(
    "ML_SERVICE_IMAGE",
    "BATCH_UI_IMAGE",
    "SPECIES_SERVICE_IMAGE",
    "ML_SERVICE_PORT",
    "BATCH_UI_PORT",
    "SPECIES_SERVICE_PORT",
    "HOST_DATA_DIR",
    "HOST_MEDIA_DIR",
    "HOST_CONFIG_DIR",
    "LOG_ROTATE_WHEN",
    "LOG_ROTATE_INTERVAL",
    "LOG_BACKUP_DAYS",
    "DB_BACKEND"
)
if ($ForMongo) {
    $required += @("MONGO_URI", "MONGO_DB_NAME")
}

$missing = @()
foreach ($key in $required) {
    if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$values[$key])) {
        $missing += $key
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Missing env keys in ${resolvedEnv}:"
    $missing | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

Write-Host "Env check passed for: $resolvedEnv"
Write-Host "Checked keys:"
$required | ForEach-Object { Write-Host "  - $_" }
