<#
.SYNOPSIS
  Run local ML/species API smoke calls for images.

.DESCRIPTION
  Read test images from `test-media/input` and write service responses to
  `test-media/output` as JSON files for quick inspection.
#>

# --- Setup + env ---
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim('"')
    }
}

$mlPort = if ($env:ML_SERVICE_PORT) { $env:ML_SERVICE_PORT } else { "8010" }
$speciesPort = if ($env:SPECIES_SERVICE_PORT) { $env:SPECIES_SERVICE_PORT } else { "8100" }

# --- Input/output folders ---
$inputDir = Join-Path (Get-Location) "test-media\\input"
$outputDir = Join-Path (Get-Location) "test-media\\output"
New-Item -ItemType Directory -Force -Path $inputDir | Out-Null
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$supported = @("*.jpg", "*.jpeg", "*.png", "*.webp")
$images = foreach ($pat in $supported) { Get-ChildItem -Path $inputDir -File -Filter $pat }
$images = $images | Sort-Object FullName -Unique

if (-not $images -or $images.Count -eq 0) {
    Write-Host "No images found in $inputDir"
    Write-Host "Copy .jpg/.jpeg/.png/.webp files into test-media/input and rerun."
    exit 1
}

# --- Service calls ---
Write-Host "Found $($images.Count) image(s). Writing JSON results to $outputDir"

foreach ($img in $images) {
    $name = [IO.Path]::GetFileNameWithoutExtension($img.Name)
    $mlOut = Join-Path $outputDir "$name.ml.json"
    $speciesOut = Join-Path $outputDir "$name.species.json"

    $mlB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($img.FullName))
    $mlBody = @{ image_base64 = $mlB64 } | ConvertTo-Json -Compress

    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$mlPort/detect-base64" -Method Post -ContentType "application/json" -Body $mlBody -TimeoutSec 120 |
            ConvertTo-Json -Depth 20 | Set-Content -Path $mlOut -Encoding UTF8
        Write-Host "Wrote $mlOut"
    } catch {
        $_ | Out-String | Set-Content -Path $mlOut -Encoding UTF8
        Write-Host "ml-service request failed for $($img.Name). See $mlOut"
    }

    try {
        $form = @{ image = Get-Item $img.FullName }
        Invoke-RestMethod -Uri "http://127.0.0.1:$speciesPort/predict" -Method Post -Form $form -TimeoutSec 180 |
            ConvertTo-Json -Depth 20 | Set-Content -Path $speciesOut -Encoding UTF8
        Write-Host "Wrote $speciesOut"
    } catch {
        $_ | Out-String | Set-Content -Path $speciesOut -Encoding UTF8
        Write-Host "species-service request failed for $($img.Name). See $speciesOut"
    }
}

Write-Host "Done. Open test-media/output to inspect results."
