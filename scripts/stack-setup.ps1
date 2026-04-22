<#
.SYNOPSIS
  Setup and start Docker stack for wildlife app.

.DESCRIPTION
  Ensures `.env` exists, optionally pulls images, then starts Docker Compose.
  Use `-Species` to include the species profile.

.PARAMETER Species
  Include species-service compose profile.

.PARAMETER Pull
  Pull images before starting containers.
#>

param(
    [switch]$Species,
    [switch]$Pull
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

foreach ($k in @("ML_SERVICE_IMAGE", "BATCH_UI_IMAGE", "SPECIES_SERVICE_IMAGE")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

if ($Pull) {
    if ($Species) {
        docker compose --env-file .env --profile species pull
    } else {
        docker compose --env-file .env pull
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Species) {
    docker compose --env-file .env --profile species up -d
} else {
    docker compose --env-file .env up -d
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker compose --env-file .env ps
