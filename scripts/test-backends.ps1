<#
.SYNOPSIS
  Run backend smoke tests (SQLite and optional Mongo) in one command.

.DESCRIPTION
  Executes pytest for backend-focused tests:
  - tests/test_backend_smoke.py
  - tests/test_ui_backend_functionality.py
  - tests/test_smoke.py

  Mongo-specific smoke checks auto-skip when pymongo or a Mongo server is not available.
#>

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Running backend smoke tests..."
python -m pytest tests/test_backend_smoke.py tests/test_ui_backend_functionality.py tests/test_smoke.py -q
if ($LASTEXITCODE -ne 0) {
    Write-Error "Backend smoke tests failed."
    exit $LASTEXITCODE
}

Write-Host "Backend smoke tests passed."
