$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

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

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg not found in PATH."
    Write-Host "Install ffmpeg, then rerun scripts/test-video.ps1."
    exit 1
}

foreach ($v in $videos) {
    $base = [IO.Path]::GetFileNameWithoutExtension($v.Name)
    $outPattern = Join-Path $framesDir ("{0}_frame_%04d.jpg" -f $base)
    ffmpeg -y -i $v.FullName -vf "fps=1" $outPattern | Out-Null
    Write-Host "Extracted frames from $($v.Name) into test-media/input"
}

& (Join-Path $PSScriptRoot "test-local.ps1")
