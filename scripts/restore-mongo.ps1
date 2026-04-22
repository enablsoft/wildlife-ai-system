<#
.SYNOPSIS
  Restore MongoDB metadata backup created by backup-mongo.ps1.

.DESCRIPTION
  Restores a Mongo dump folder (or zip) into the target Mongo database using
  `mongorestore`. By default, this uses `--drop` so existing collections in the
  database are replaced.

.PARAMETER BackupPath
  Path to Mongo dump folder or zip file.

.PARAMETER MongoUri
  Mongo connection string. Defaults to env `MONGO_URI` or localhost.

.PARAMETER DatabaseName
  Mongo database name. Defaults to env `MONGO_DB_NAME` or wildlife_webapp.

.PARAMETER NoDrop
  Do not pass `--drop` to mongorestore.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$MongoUri = "",
    [string]$DatabaseName = "",
    [switch]$NoDrop
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Get-Command mongorestore -ErrorAction SilentlyContinue)) {
    Write-Error "mongorestore is not installed or not on PATH. Install MongoDB Database Tools first."
}

if (-not $MongoUri) {
    $MongoUri = if ($env:MONGO_URI) { $env:MONGO_URI } else { "mongodb://127.0.0.1:27017" }
}
if (-not $DatabaseName) {
    $DatabaseName = if ($env:MONGO_DB_NAME) { $env:MONGO_DB_NAME } else { "wildlife_webapp" }
}

$resolvedPath = if ([System.IO.Path]::IsPathRooted($BackupPath)) { $BackupPath } else { Join-Path $repoRoot $BackupPath }
if (-not (Test-Path $resolvedPath)) {
    Write-Error "Backup path not found: $resolvedPath"
}

$stagingRoot = $null
$dumpRoot = $resolvedPath
if ($resolvedPath.ToLower().EndsWith(".zip")) {
    $stagingRoot = Join-Path $repoRoot ("backups/restore_mongo_staging_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
    Expand-Archive -Path $resolvedPath -DestinationPath $stagingRoot -Force
    $dumpRoot = $stagingRoot
}

try {
    $dbDumpPath = Join-Path $dumpRoot $DatabaseName
    if (-not (Test-Path $dbDumpPath)) {
        # Accept one-level nested directory layouts too.
        $candidates = Get-ChildItem -Path $dumpRoot -Directory -ErrorAction SilentlyContinue
        foreach ($c in $candidates) {
            $nested = Join-Path $c.FullName $DatabaseName
            if (Test-Path $nested) {
                $dbDumpPath = $nested
                break
            }
        }
    }
    if (-not (Test-Path $dbDumpPath)) {
        Write-Error "Could not find database dump folder '$DatabaseName' under: $dumpRoot"
    }

    $args = @("--uri=$MongoUri", "--db=$DatabaseName", $dbDumpPath)
    if (-not $NoDrop) {
        $args = @("--drop") + $args
    }

    Write-Host "Running mongorestore..."
    & mongorestore @args
    if ($LASTEXITCODE -ne 0) {
        Write-Error "mongorestore failed with exit code $LASTEXITCODE"
    }
    Write-Host "Mongo restore complete for database:"
    Write-Host "  $DatabaseName"
}
finally {
    if ($stagingRoot) {
        Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
    }
}
