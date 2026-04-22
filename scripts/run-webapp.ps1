<#
.SYNOPSIS
  Run the FastAPI webapp locally.

.DESCRIPTION
  Ensure ffmpeg availability for video workflows, install webapp Python
  requirements, then launch uvicorn on localhost:8110.
#>

# --- Setup ---
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Ensure-Ffmpeg {
    # Best-effort auto-install path for Windows machines.
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

# --- Dependencies + app launch ---
Ensure-Ffmpeg
python -m pip install -r requirements-webapp.txt
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8110
