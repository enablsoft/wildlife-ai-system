$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Ensure-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        return
    }
    Write-Host "ffmpeg not found. Trying auto-install via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget unavailable. Install ffmpeg manually for video processing support."
        return
    }
    winget install --id Gyan.FFmpeg -e --source winget --accept-package-agreements --accept-source-agreements
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Write-Host "ffmpeg may need a terminal restart to appear in PATH."
    }
}

Ensure-Ffmpeg
python -m pip install -r requirements-webapp.txt
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8110
