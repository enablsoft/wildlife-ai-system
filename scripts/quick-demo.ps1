<#
.SYNOPSIS
  Pull published images, write .env from .env.demo if needed, start Docker stack, wait for /health.

.DESCRIPTION
  For a fast local smoke test without building images. Does not run git commit.
  After this script succeeds, run .\scripts\run-webapp.ps1 in another terminal and open http://127.0.0.1:8110

.PARAMETER Species
  Include the species classification service (Compose profile "species").

.PARAMETER OverwriteEnv
  Replace existing .env with contents of .env.demo (default: only create .env when missing).
#>
param(
    [switch]$Species,
    [switch]$OverwriteEnv
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Docker Compose prefers shell environment over --env-file; clear image vars so .env wins.
foreach ($k in @("ML_SERVICE_IMAGE", "BATCH_UI_IMAGE", "SPECIES_SERVICE_IMAGE")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

$demoEnv = Join-Path $repoRoot ".env.demo"
$targetEnv = Join-Path $repoRoot ".env"
if (-not (Test-Path $demoEnv)) {
    Write-Error "Missing .env.demo - expected at $demoEnv"
}

if (-not (Test-Path $targetEnv) -or $OverwriteEnv) {
    Copy-Item $demoEnv $targetEnv -Force
    Write-Host "Wrote .env from .env.demo"
} else {
    Write-Host "Using existing .env (use -OverwriteEnv to replace with .env.demo)"
}

foreach ($d in @("data", "media", "config")) {
    $p = Join-Path $repoRoot $d
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p | Out-Null
        Write-Host "Created directory $d/"
    }
}

Write-Host "Pulling images..."
docker compose --env-file .env pull

if ($Species) {
    Write-Host "Starting stack (with species profile)..."
    docker compose --env-file .env --profile species up -d
} else {
    Write-Host "Starting stack (ML + batch UI only)..."
    docker compose --env-file .env up -d
}

$healthChecks = @(
    @{ Name = "ML service"; Url = "http://127.0.0.1:8010/health" }
    @{ Name = "Batch UI";   Url = "http://127.0.0.1:8090/health" }
)
if ($Species) {
    $healthChecks += @{ Name = "Species service"; Url = "http://127.0.0.1:8100/health" }
}

Write-Host "Waiting for health endpoints (containers may take a few minutes on first start)..."
$deadline = (Get-Date).AddMinutes(8)
$allOk = $false
while ((Get-Date) -lt $deadline) {
    $ok = $true
    foreach ($h in $healthChecks) {
        try {
            $r = Invoke-WebRequest -Uri $h.Url -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
            if ($r.StatusCode -ne 200) { $ok = $false }
        } catch {
            $ok = $false
        }
    }
    if ($ok) {
        $allOk = $true
        break
    }
    Start-Sleep -Seconds 4
}

if (-not $allOk) {
    Write-Warning "Health checks did not all return HTTP 200 before timeout. Inspect: docker compose --env-file .env ps"
    Write-Host "Try: docker compose --env-file .env logs ml-service"
    exit 1
}

Write-Host ""
Write-Host "Demo stack is up."
foreach ($h in $healthChecks) {
    Write-Host "  OK  $($h.Name): $($h.Url)"
}
Write-Host ""
Write-Host "Next (separate terminal):"
Write-Host "  .\scripts\run-webapp.ps1"
Write-Host "Then open: http://127.0.0.1:8110"
Write-Host ""
Write-Host 'Stop: docker compose --env-file .env down'
