# Wildlife AI System (Deploy Only)

Public deployment repo for prebuilt containers:

- `ghcr.io/enablsoft/wildlife-ai-ml-service`
- `ghcr.io/enablsoft/wildlife-ai-batch-ui`
- `ghcr.io/enablsoft/wildlife-ai-species-service` (optional species profile)

This repo intentionally contains only runtime configuration, compose files, and helper scripts.

## Quick start

1. Copy `.env.example` to `.env` and adjust image tags/ports if needed.
2. Start core stack:

```powershell
docker compose --env-file .env up -d
```

Optional: include species service:

```powershell
docker compose --env-file .env --profile species up -d
```

3. Verify:

- Detector: `http://localhost:8010/health`
- Batch UI: `http://localhost:8090/health`
- Species (if enabled): `http://localhost:8100/health`

4. Stop:

```powershell
docker compose --env-file .env down
```

## Configure paths

- Host `./data` mounts to `/data` in `batch-ui`.
- Host `./media` mounts to `/data/media` in `batch-ui`.
- Host `./config` mounts read-only to `/app/config`.

## Species service

- `species-service` is dockerized as an **optional profile** (`species`) so the base stack still runs if species image is not needed.
- Enable it only when you want species classification endpoints:
  - `docker compose --env-file .env --profile species up -d`
- Configure image/tag via `SPECIES_SERVICE_IMAGE` in `.env`.

## Test-media workflow (terminal)

Use the built-in test workspace:

- Put test images in `test-media/input/` (`.jpg`, `.jpeg`, `.png`, `.webp`).
- Run image test from terminal:

```powershell
.\scripts\test-local.ps1
```

- Inspect results in `test-media/output/`:
  - `<name>.ml.json` (MegaDetector response)
  - `<name>.species.json` (SpeciesNet response)

Video test from terminal:

1. Put a video in `test-media/video/` (`.mp4`, `.mov`, `.avi`, `.mkv`).
2. Run:

```powershell
.\scripts\test-video.ps1
```

This extracts frames to `test-media/input/` and then runs `test-local.ps1`, writing JSON outputs to `test-media/output/`.

## Python web app interface (automatic processing)

You can run a local Python web app that does:

- upload image/video
- video -> frame extraction
- detector + species calls
- annotated images with bounding boxes
- JSON results saved to `test-media/output/run_<timestamp>/`
- queue + run history in SQLite (`data/webapp_jobs.sqlite`)
- pause/resume processing (n8n-like run control)
- retry/cancel controls from the UI
- inline preview of generated annotated frames/images

Start services first (include species):

```powershell
docker compose --env-file .env --profile species up -d
```

Run web app:

```powershell
.\scripts\run-webapp.ps1
```

Open:

- `http://localhost:8110`
- The app binds to `127.0.0.1` (localhost only) by default.

To include species outputs, start stack with species profile first:

```powershell
docker compose --env-file .env --profile species up -d
```

`config/stack.example.json` shows container-side defaults for:

- `optional_batch.job_db_path_in_container`
- `optional_batch.media_root_in_container`

If you want overrides, copy `config/stack.example.json` to `config/stack.json` and edit it.

## Visibility model

- Keep this repository public or private as desired.
- GHCR package visibility is managed separately per package.
