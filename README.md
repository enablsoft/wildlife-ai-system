# Wildlife AI System (Deploy Only)

Public deployment repo for prebuilt containers:

- `ghcr.io/enablsoft/wildlife-ai-ml-service`
- `ghcr.io/enablsoft/wildlife-ai-batch-ui`

This repo intentionally contains only runtime configuration, compose files, and helper scripts.

## Quick start

1. Copy `.env.example` to `.env` and adjust image tags/ports if needed.
2. Start:

```powershell
docker compose --env-file .env up -d
```

3. Verify:

- Detector: `http://localhost:8010/health`
- Batch UI: `http://localhost:8090/health`

4. Stop:

```powershell
docker compose --env-file .env down
```

## Configure paths

- Host `./data` mounts to `/data` in `batch-ui`.
- Host `./media` mounts to `/data/media` in `batch-ui`.
- Host `./config` mounts read-only to `/app/config`.

`config/stack.example.json` shows container-side defaults for:

- `optional_batch.job_db_path_in_container`
- `optional_batch.media_root_in_container`

If you want overrides, copy `config/stack.example.json` to `config/stack.json` and edit it.

## Visibility model

- Keep this repository public or private as desired.
- GHCR package visibility is managed separately per package.
