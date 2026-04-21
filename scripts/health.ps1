$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim('"')
    }
}

$ml = if ($env:ML_SERVICE_PORT) { $env:ML_SERVICE_PORT } else { "8010" }
$ui = if ($env:BATCH_UI_PORT) { $env:BATCH_UI_PORT } else { "8090" }

Invoke-RestMethod -Uri "http://127.0.0.1:$ml/health" -TimeoutSec 20 | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:$ui/health" -TimeoutSec 20 | ConvertTo-Json
