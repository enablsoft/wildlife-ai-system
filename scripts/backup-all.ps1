<#
.SYNOPSIS
  Create a timestamped local backup archive.

.DESCRIPTION
  Creates a zip backup under `backups/` that includes:
  - SQLite DB (`data/webapp_jobs.sqlite`) when present
  - logs folder (`logs/`) when present
  - optionally generated media folders (`test-media/output`, `test-media/input`, `test-media/video`)

.PARAMETER IncludeMedia
  Include test-media folders in backup (can be large).
#>

param(
    [switch]$IncludeMedia
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $repoRoot "backups"
$stagingRoot = Join-Path $backupRoot ("staging_" + $timestamp)
$zipPath = Join-Path $backupRoot ("wildlife_backup_" + $timestamp + ".zip")

New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
if (Test-Path $stagingRoot) {
    Remove-Item -Recurse -Force $stagingRoot
}
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

function Copy-IfExists {
    param(
        [string]$SourcePath,
        [string]$DestPath
    )
    if (Test-Path $SourcePath) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestPath) | Out-Null
        Copy-Item -Path $SourcePath -Destination $DestPath -Recurse -Force
        return $true
    }
    return $false
}

$included = @()

if (Copy-IfExists -SourcePath (Join-Path $repoRoot "data/webapp_jobs.sqlite") -DestPath (Join-Path $stagingRoot "data/webapp_jobs.sqlite")) {
    $included += "data/webapp_jobs.sqlite"
}
if (Copy-IfExists -SourcePath (Join-Path $repoRoot "logs") -DestPath (Join-Path $stagingRoot "logs")) {
    $included += "logs/"
}

if ($IncludeMedia) {
    if (Copy-IfExists -SourcePath (Join-Path $repoRoot "test-media/output") -DestPath (Join-Path $stagingRoot "test-media/output")) {
        $included += "test-media/output/"
    }
    if (Copy-IfExists -SourcePath (Join-Path $repoRoot "test-media/input") -DestPath (Join-Path $stagingRoot "test-media/input")) {
        $included += "test-media/input/"
    }
    if (Copy-IfExists -SourcePath (Join-Path $repoRoot "test-media/video") -DestPath (Join-Path $stagingRoot "test-media/video")) {
        $included += "test-media/video/"
    }
}

if ($included.Count -eq 0) {
    Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
    Write-Warning "Nothing to back up. No DB/logs/media paths found."
    exit 1
}

Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue

Write-Host "Backup created:"
Write-Host "  $zipPath"
Write-Host "Included:"
$included | ForEach-Object { Write-Host "  - $_" }
