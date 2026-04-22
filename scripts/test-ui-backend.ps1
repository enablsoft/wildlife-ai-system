<#
.SYNOPSIS
  Run backend functionality checks for UI actions.

.DESCRIPTION
  Executes the UI-backend functionality tests and the baseline smoke test in
  one command, then returns a non-zero exit code if either test set fails.
#>

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Running UI backend functionality tests..."
python -m pytest tests/test_ui_backend_functionality.py -q
if ($LASTEXITCODE -ne 0) {
    Write-Error "UI backend functionality tests failed."
    exit $LASTEXITCODE
}

Write-Host "Running smoke test..."
python -m pytest tests/test_smoke.py -q
if ($LASTEXITCODE -ne 0) {
    Write-Error "Smoke test failed."
    exit $LASTEXITCODE
}

Write-Host "All UI backend and smoke tests passed."
