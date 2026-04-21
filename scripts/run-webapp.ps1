$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -r requirements-webapp.txt
python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8110
