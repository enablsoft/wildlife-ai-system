param(
    [switch]$Species
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# So values in .env are not overridden by stray shell env (Compose prefers shell over --env-file).
foreach ($k in @("ML_SERVICE_IMAGE", "BATCH_UI_IMAGE", "SPECIES_SERVICE_IMAGE")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

if ($Species) {
    docker compose --env-file .env --profile species up -d
} else {
    docker compose --env-file .env up -d
}
Write-Host "Started stack. Check /health endpoints."
