<#
.SYNOPSIS
  Start Docker services for local development.

.DESCRIPTION
  Load or create `.env`, clear conflicting image env vars, and start
  Docker Compose (optionally with the species profile).

.PARAMETER Species
  Include species-service compose profile.
#>
param(
    [switch]$Species
)

# --- Setup ---
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "Preflight: checking remote and local repo status..."
& "$PSScriptRoot\check-repo-state.ps1"

# So values in .env are not overridden by stray shell env (Compose prefers shell over --env-file).
foreach ($k in @("ML_SERVICE_IMAGE", "BATCH_UI_IMAGE", "SPECIES_SERVICE_IMAGE")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

# --- Start stack ---
if ($Species) {
    docker compose --env-file .env --profile species up -d
} else {
    docker compose --env-file .env up -d
}
Write-Host "Started stack. Check /health endpoints."
