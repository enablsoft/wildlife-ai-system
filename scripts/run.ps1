param(
    [switch]$Species
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

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
