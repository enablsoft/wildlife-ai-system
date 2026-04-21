$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -r requirements-webapp.txt
python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8110
