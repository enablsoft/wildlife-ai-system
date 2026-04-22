<#
.SYNOPSIS
  Extract frames from test videos then run local image tests.

.DESCRIPTION
  Ensure ffmpeg, extract 1 FPS frames from `test-media/video` into
  `test-media/input`, then invoke `scripts/test-local.ps1`.
#>

# --- Setup ---
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Ensure-Ffmpeg {
    # Best-effort installer for developer convenience on Windows.
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        return $true
    }
    Write-Host "ffmpeg not found. Attempting automatic install via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget is not available. Install ffmpeg manually and rerun."
        return $false
    }
    winget install --id Gyan.FFmpeg -e --source winget --accept-package-agreements --accept-source-agreements
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ff) {
        return $true
    }
    Write-Host "ffmpeg install may require terminal restart. Close/reopen terminal and rerun."
    return $false
}

# --- Discover inputs ---
$videoDir = Join-Path (Get-Location) "test-media\\video"
$framesDir = Join-Path (Get-Location) "test-media\\input"

New-Item -ItemType Directory -Force -Path $videoDir | Out-Null
New-Item -ItemType Directory -Force -Path $framesDir | Out-Null

$videos = Get-ChildItem -Path $videoDir -File -Include *.mp4,*.mov,*.avi,*.mkv -ErrorAction SilentlyContinue
if (-not $videos -or $videos.Count -eq 0) {
    Write-Host "No video files found in $videoDir"
    Write-Host "Copy a video into test-media/video and rerun."
    exit 1
}

if (-not (Ensure-Ffmpeg)) {
    exit 1
}

# --- Extract + run downstream image tests ---
foreach ($v in $videos) {
    $base = [IO.Path]::GetFileNameWithoutExtension($v.Name)
    $outPattern = Join-Path $framesDir ("{0}_frame_%04d.jpg" -f $base)
    ffmpeg -y -i $v.FullName -vf "fps=1" $outPattern | Out-Null
    Write-Host "Extracted frames from $($v.Name) into test-media/input"
}

& (Join-Path $PSScriptRoot "test-local.ps1")
