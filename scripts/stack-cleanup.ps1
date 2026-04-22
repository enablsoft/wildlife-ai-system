<#
.SYNOPSIS
  Stop and clean Docker resources for wildlife stack.

.DESCRIPTION
  Stops compose services and optionally removes images, named volumes, and
  dangling Docker resources.

.PARAMETER Species
  Include species profile during compose down.

.PARAMETER RemoveImages
  Remove images referenced by the compose file (`docker compose down --rmi all`).

.PARAMETER RemoveVolumes
  Remove named volumes (`docker compose down -v`).

.PARAMETER PruneDangling
  Run `docker image prune -f` after compose down.

.PARAMETER Preview
  Print planned commands without executing them.
#>

param(
    [switch]$Species,
    [switch]$RemoveImages,
    [switch]$RemoveVolumes,
    [switch]$PruneDangling,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$args = @("--env-file", ".env")
if ($Species) { $args += @("--profile", "species") }
$args += "down"
if ($RemoveImages) { $args += @("--rmi", "all") }
if ($RemoveVolumes) { $args += "-v" }

Write-Host "Compose command: docker compose $($args -join ' ')"
if (-not $Preview) {
    docker compose @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($PruneDangling) {
    Write-Host "Prune command: docker image prune -f"
    if (-not $Preview) {
        docker image prune -f
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

if (-not $Preview) {
    docker compose --env-file .env ps
}
