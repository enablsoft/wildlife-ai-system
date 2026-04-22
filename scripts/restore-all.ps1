<#
.SYNOPSIS
  Restore a SQLite-mode backup archive created by backup-all.ps1.

.DESCRIPTION
  Extracts a backup zip from `backups/` and restores files into the repo:
  - `data/webapp_jobs.sqlite`
  - `logs/`
  - optional `test-media/*` folders if they exist in the archive

  For safety, existing targets are not overwritten unless `-Force` is provided.

.PARAMETER ArchivePath
  Path to backup zip. Relative paths are resolved from repo root.

.PARAMETER Force
  Overwrite existing files/folders when restoring.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$resolvedArchive = if ([System.IO.Path]::IsPathRooted($ArchivePath)) { $ArchivePath } else { Join-Path $repoRoot $ArchivePath }
if (-not (Test-Path $resolvedArchive)) {
    Write-Error "Backup archive not found: $resolvedArchive"
}

$stagingRoot = Join-Path $repoRoot ("backups/restore_staging_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

try {
    Expand-Archive -Path $resolvedArchive -DestinationPath $stagingRoot -Force

    $restoreMap = @(
        @{ Source = (Join-Path $stagingRoot "data/webapp_jobs.sqlite"); Target = (Join-Path $repoRoot "data/webapp_jobs.sqlite"); Kind = "file" }
        @{ Source = (Join-Path $stagingRoot "logs"); Target = (Join-Path $repoRoot "logs"); Kind = "dir" }
        @{ Source = (Join-Path $stagingRoot "test-media/output"); Target = (Join-Path $repoRoot "test-media/output"); Kind = "dir" }
        @{ Source = (Join-Path $stagingRoot "test-media/input"); Target = (Join-Path $repoRoot "test-media/input"); Kind = "dir" }
        @{ Source = (Join-Path $stagingRoot "test-media/video"); Target = (Join-Path $repoRoot "test-media/video"); Kind = "dir" }
    )

    $planned = @()
    foreach ($item in $restoreMap) {
        if (-not (Test-Path $item.Source)) {
            continue
        }
        if ((Test-Path $item.Target) -and (-not $Force)) {
            Write-Error "Target exists: $($item.Target). Re-run with -Force to overwrite."
        }
        $planned += $item
    }

    if ($planned.Count -eq 0) {
        Write-Error "Archive does not contain known restore paths."
    }

    foreach ($item in $planned) {
        if (Test-Path $item.Target) {
            Remove-Item -Recurse -Force $item.Target
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Target) | Out-Null
        Copy-Item -Path $item.Source -Destination $item.Target -Recurse -Force
    }

    Write-Host "Restore complete from:"
    Write-Host "  $resolvedArchive"
    Write-Host "Restored:"
    $planned | ForEach-Object { Write-Host "  - $($_.Target)" }
}
finally {
    Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
}
