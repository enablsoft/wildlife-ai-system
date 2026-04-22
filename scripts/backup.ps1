<#
.SYNOPSIS
  Unified backup entrypoint for SQLite or Mongo backend.

.DESCRIPTION
  Auto-selects backup mode from DB_BACKEND (env var or .env):
  - sqlite -> scripts/backup-all.ps1
  - mongo  -> scripts/backup-mongo.ps1

.PARAMETER IncludeMedia
  SQLite mode only: include test-media folders.

.PARAMETER Zip
  Mongo mode only: zip Mongo dump and remove raw folder.

.PARAMETER MongoUri
  Mongo mode only: override MONGO_URI.

.PARAMETER DatabaseName
  Mongo mode only: override MONGO_DB_NAME.

.PARAMETER Interactive
  Ask for confirmation before running backup.
#>

param(
    [switch]$IncludeMedia,
    [switch]$Zip,
    [string]$MongoUri = "",
    [string]$DatabaseName = "",
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Get-EnvValue {
    param(
        [string]$Key,
        [string]$DefaultValue = ""
    )
    $envValue = [Environment]::GetEnvironmentVariable($Key)
    if ($envValue) {
        return [string]$envValue
    }
    $envFile = Join-Path $repoRoot ".env"
    if (-not (Test-Path $envFile)) {
        return $DefaultValue
    }
    $line = Select-String -Path $envFile -Pattern ("^\s*" + [regex]::Escape($Key) + "\s*=") | Select-Object -First 1
    if (-not $line) {
        return $DefaultValue
    }
    $raw = ($line.Line -split "=", 2)[1].Trim()
    return $raw.Trim("'`"")
}

$backend = (Get-EnvValue -Key "DB_BACKEND" -DefaultValue "sqlite").Trim().ToLower()
if (-not $backend) {
    $backend = "sqlite"
}

if ($Interactive) {
    $answer = Read-Host "Proceed with backup for DB_BACKEND=$backend? [y/N]"
    if ($answer.Trim().ToLower() -notin @("y", "yes")) {
        Write-Host "Cancelled."
        exit 0
    }
}

if ($backend -eq "mongo") {
    if ($IncludeMedia) {
        Write-Warning "-IncludeMedia is SQLite-only and will be ignored for Mongo backups."
    }
    Write-Host "DB_BACKEND=mongo -> running scripts/backup-mongo.ps1"
    $args = @()
    if ($Zip) { $args += "-Zip" }
    if ($MongoUri) { $args += @("-MongoUri", $MongoUri) }
    if ($DatabaseName) { $args += @("-DatabaseName", $DatabaseName) }
    & (Join-Path $PSScriptRoot "backup-mongo.ps1") @args
    exit $LASTEXITCODE
}

Write-Host "DB_BACKEND=sqlite -> running scripts/backup-all.ps1"
if ($Zip -or $MongoUri -or $DatabaseName) {
    Write-Warning "Mongo-only flags (-Zip, -MongoUri, -DatabaseName) are ignored in SQLite mode."
}
$sqliteArgs = @()
if ($IncludeMedia) { $sqliteArgs += "-IncludeMedia" }
& (Join-Path $PSScriptRoot "backup-all.ps1") @sqliteArgs
exit $LASTEXITCODE
