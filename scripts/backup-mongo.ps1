<#
.SYNOPSIS
  Create a MongoDB dump for wildlife webapp metadata.

.DESCRIPTION
  Runs `mongodump` for the database configured by `MONGO_URI` and
  `MONGO_DB_NAME` (or script parameters), and stores output under `backups/`.
  Optionally archives the dump as a zip and removes the raw dump folder.

.PARAMETER MongoUri
  Mongo connection string. Defaults to env `MONGO_URI` or localhost.

.PARAMETER DatabaseName
  Mongo database name. Defaults to env `MONGO_DB_NAME` or wildlife_webapp.

.PARAMETER Zip
  Compress dump folder into a zip and remove the uncompressed folder.
#>

param(
    [string]$MongoUri = "",
    [string]$DatabaseName = "",
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Get-Command mongodump -ErrorAction SilentlyContinue)) {
    Write-Error "mongodump is not installed or not on PATH. Install MongoDB Database Tools first."
}

if (-not $MongoUri) {
    $MongoUri = if ($env:MONGO_URI) { $env:MONGO_URI } else { "mongodb://127.0.0.1:27017" }
}
if (-not $DatabaseName) {
    $DatabaseName = if ($env:MONGO_DB_NAME) { $env:MONGO_DB_NAME } else { "wildlife_webapp" }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $repoRoot "backups"
$dumpDir = Join-Path $backupRoot ("mongo_dump_" + $DatabaseName + "_" + $timestamp)

New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

Write-Host "Running mongodump..."
& mongodump --uri="$MongoUri" --db="$DatabaseName" --out="$dumpDir"
if ($LASTEXITCODE -ne 0) {
    Write-Error "mongodump failed with exit code $LASTEXITCODE"
}

if ($Zip) {
    $zipPath = $dumpDir + ".zip"
    Compress-Archive -Path (Join-Path $dumpDir "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
    Remove-Item -Recurse -Force $dumpDir -ErrorAction SilentlyContinue
    Write-Host "Mongo backup created:"
    Write-Host "  $zipPath"
} else {
    Write-Host "Mongo backup created:"
    Write-Host "  $dumpDir"
}
