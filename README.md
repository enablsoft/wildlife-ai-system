# Wildlife AI System (public deploy)

Deploy **prebuilt container images** for camera-trap style workflows: MegaDetector-style detection, optional species classification, and a batch UI—plus a **local Python web app** for uploads, queues, and annotated previews.

This repository holds **runtime configuration** (Compose, env templates, scripts, and the web app). Application source for the images lives in the upstream build repo; images are published to **GHCR**.

---

## First-time setup (new clone)

Do these steps **once** after you clone the repository.

| Step | What to do |
|------|------------|
| **1** | **Create your local env file.** The repo ships **`.env.example`** (template only). Copy it to **`.env`** and edit values there. |
| **2** | **Never commit `.env`.** It is [gitignored](.gitignore) so secrets and machine-specific paths stay off GitHub. Only **`.env.example`** is committed so others know which variables exist. |
| **3** | **Set image tags** in `.env`: use `:local` only if you built images yourself; otherwise set tags that exist in GHCR for your org (see [Container images](#container-images-ghcr)). |
| **4** | Start Docker Compose (see [Quick start](#quick-start-docker)) or on Windows run **`.\scripts\run.ps1`** (creates `.env` from `.env.example` if missing). Add **`-Species`** to that script to include the species service. |

**Copy commands**

```powershell
# Windows (PowerShell), from repo root
Copy-Item .env.example .env
notepad .env   # or your editor
```

```bash
# macOS / Linux, from repo root
cp .env.example .env
${EDITOR:-nano} .env
```

Some scripts (for example **`scripts\test-local.ps1`**) copy **`.env.example` → `.env`** automatically if `.env` is missing—still review `.env` before relying on it in production.

---

## Contents

| Area | What you use it for |
|------|---------------------|
| **`.env.example`** | Committed template—copy to `.env` and customize |
| **`docker-compose.yml`** | ML service, batch UI, optional species profile |
| **`scripts/`** | PowerShell: stack health, tests, web app, `run.ps1` helper |
| **`webapp/`** | FastAPI UI: queue, runs, frame browser, batch folder enqueue |
| **`test-media/`** | Local input/output/video workspace (outputs are gitignored) |
| **`config/`** | Optional `stack.json` overrides (see `stack.example.json`) |

---

## Container images (GHCR)

| Image | Role |
|-------|------|
| `ghcr.io/enablsoft/wildlife-ai-ml-service` | Detector / ML API |
| `ghcr.io/enablsoft/wildlife-ai-batch-ui` | Batch UI |
| `ghcr.io/enablsoft/wildlife-ai-species-service` | Optional species classification |

Set full image names and tags in **`.env`** (see **`.env.example`**). If images are **private** on GHCR, run `docker login ghcr.io` before `docker compose pull`.

---

## Prerequisites

- **Docker** and **Docker Compose** v2  
- **Windows**: PowerShell for the scripts below  
- **Optional**: [ffmpeg](https://ffmpeg.org/) on the host for video workflows (scripts may offer `winget` install on Windows)  
- **Python 3.10+** and a venv if you run the local web app (see [Local web app](#local-web-app))

---

## Quick start (Docker)

1. Ensure **`.env`** exists (copy from **`.env.example`** if needed—see [First-time setup](#first-time-setup-new-clone)).

2. **Start the core stack**

   ```powershell
   docker compose --env-file .env up -d
   ```

   On Windows you can instead run **`.\scripts\run.ps1`** (add **`-Species`** to start the species profile too).

3. **Optional — include species service**

   ```powershell
   docker compose --env-file .env --profile species up -d
   ```

4. **Health checks** (defaults match **`.env.example`**)

   | Service | URL |
   |---------|-----|
   | Detector (ML) | http://localhost:8010/health |
   | Batch UI | http://localhost:8090/health |
   | Species (if enabled) | http://localhost:8100/health |

5. **Stop**

   ```powershell
   docker compose --env-file .env down
   ```

---

## Host paths (batch UI)

Volumes are wired in `docker-compose.yml` via **`.env`**:

| Variable | Default | Mounted into `batch-ui` as |
|----------|---------|----------------------------|
| `HOST_DATA_DIR` | `./data` | `/data` |
| `HOST_MEDIA_DIR` | `./media` | `/data/media` |
| `HOST_CONFIG_DIR` | `./config` | `/app/config` (read-only) |

---

## Species service

- Declared as Compose **profile** `species` so the base stack runs without it.
- Enable when you need species endpoints:

  ```powershell
  docker compose --env-file .env --profile species up -d
  ```

- Set `SPECIES_SERVICE_IMAGE` in **`.env`** if you use a custom tag.

---

## Local web app

A **FastAPI** app in `webapp/` provides a browser UI for local processing (uploads, video frame extraction, detector + species calls, annotated outputs, SQLite job history).

**Features (summary)**

- Upload images/videos; extract frames from video  
- Call detector and species services; save JSON and annotated images under `test-media/output/run_<timestamp>/`  
- Job queue, run history, pause/resume, retry/cancel  
- Frame results with search and pagination; **Video / source summary** table  
- **Settings** tab: single control for hiding blank/no-match frames (applies to frame list and video frame browser)  
- Batch enqueue from a folder path; output browser and **Open folder** (OS) for completed jobs  

**Run**

1. Start the stack (include species if you want species labels):

   ```powershell
   docker compose --env-file .env --profile species up -d
   ```

2. Start the web app:

   ```powershell
   .\scripts\run-webapp.ps1
   ```

3. Open **http://127.0.0.1:8110** (localhost-only bind by default).

`run-webapp.ps1` installs Python dependencies into your environment and may attempt **ffmpeg** via `winget` on Windows if missing.

**Batch folder flow**

1. Use **Batch queue from folder** with a local path and file extensions.  
2. **Enqueue folder** and watch progress in **Runs**.  
3. Use **Output browser** / **Open folder** on finished jobs.

**Advanced:** copy `config/stack.example.json` to `config/stack.json` to override container-side paths (see `optional_batch.*` keys).

---

## Terminal tests (`test-media`)

| Script | Purpose |
|--------|---------|
| `.\scripts\test-local.ps1` | Process images in `test-media/input/` → write `*.ml.json` / `*.species.json` under `test-media/output/` |
| `.\scripts\test-video.ps1` | Extract frames from a video in `test-media/video/` into `test-media/input/`, then run `test-local.ps1` |

Supported image types: `.jpg`, `.jpeg`, `.png`, `.webp`. Video: `.mp4`, `.mov`, `.avi`, `.mkv`.

---

## Repository visibility and GHCR

- This repo can be **public or private** independently of **GHCR package** visibility (configure packages in GitHub org/user settings).

**Linking packages to this repo**

Images built from the upstream project can set `org.opencontainers.image.source` to **this** repository. After images are **rebuilt and pushed** to GHCR, GitHub can associate the package with this README.

For packages published before that label existed: open each package → **Package settings** → **Connect repository** and select this repo. See [Connecting a repository to a package](https://docs.github.com/packages/managing-container-images-with-github-container-registry/connecting-a-repository-to-a-container-image).
