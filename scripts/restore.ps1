<#
.SYNOPSIS
  Unified restore entrypoint for SQLite or Mongo backend.

.DESCRIPTION
  Auto-selects restore mode from DB_BACKEND (env var or .env):
  - sqlite -> scripts/restore-all.ps1
  - mongo  -> scripts/restore-mongo.ps1

.PARAMETER BackupPath
  Path to backup archive/folder.
  - sqlite: zip from backup-all.ps1
  - mongo: folder or zip from backup-mongo.ps1

.PARAMETER Force
  SQLite mode only: overwrite existing restore targets.

.PARAMETER NoDrop
  Mongo mode only: do not drop existing collections.

.PARAMETER MongoUri
  Mongo mode only: override MONGO_URI.

.PARAMETER DatabaseName
  Mongo mode only: override MONGO_DB_NAME.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [switch]$Force,
    [switch]$NoDrop,
    [string]$MongoUri = "",
    [string]$DatabaseName = ""
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

if ($backend -eq "mongo") {
    if ($Force) {
        Write-Warning "-Force is SQLite-only and will be ignored in Mongo restore mode."
    }
    Write-Host "DB_BACKEND=mongo -> running scripts/restore-mongo.ps1"
    $scriptPath = Join-Path $PSScriptRoot "restore-mongo.ps1"
    if ($NoDrop) {
        & $scriptPath -BackupPath $BackupPath -NoDrop -MongoUri $MongoUri -DatabaseName $DatabaseName
    } else {
        & $scriptPath -BackupPath $BackupPath -MongoUri $MongoUri -DatabaseName $DatabaseName
    }
    exit $LASTEXITCODE
}

Write-Host "DB_BACKEND=sqlite -> running scripts/restore-all.ps1"
if ($NoDrop -or $MongoUri -or $DatabaseName) {
    Write-Warning "Mongo-only flags (-NoDrop, -MongoUri, -DatabaseName) are ignored in SQLite mode."
}
$sqliteScript = Join-Path $PSScriptRoot "restore-all.ps1"
if ($Force) {
    & $sqliteScript -ArchivePath $BackupPath -Force
} else {
    & $sqliteScript -ArchivePath $BackupPath
}
exit $LASTEXITCODE
