from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from webapp.jobs_db import JobsDb
from webapp.pipeline import SUPPORTED_IMAGES, SUPPORTED_VIDEOS, extract_frames, process_images

ROOT = Path(__file__).resolve().parents[1]
TEST_MEDIA = ROOT / "test-media"
IN_DIR = TEST_MEDIA / "input"
VID_DIR = TEST_MEDIA / "video"
OUT_DIR = TEST_MEDIA / "output"
DB_PATH = ROOT / "data" / "webapp_jobs.sqlite"

app = FastAPI(title="Wildlife Media Processor", version="0.2.0")
app.mount("/files", StaticFiles(directory=str(ROOT)), name="files")
db = JobsDb(DB_PATH)
_stop_worker = False


def _render_page(msg: str = "") -> str:
    jobs = db.list_jobs(limit=200)
    paused = db.is_paused()
    job_items: list[str] = []
    for j in jobs:
        preview = ""
        if j.get("outputs_json"):
            try:
                outputs = json.loads(j["outputs_json"])
                if outputs:
                    ann = Path(outputs[0]["annotated"]).relative_to(ROOT).as_posix()
                    preview = f"<br/><img src='/files/{ann}' style='max-width:380px;border:1px solid #ccc'/>"
            except Exception:
                preview = ""
        logs = (j.get("logs") or "").strip().splitlines()
        last_log = logs[-1] if logs else ""
        err = j.get("error_text") or ""
        actions = ""
        if j["status"] in ("error", "cancelled"):
            actions += f"<a href='/retry/{j['id']}'>retry</a> "
        if j["status"] == "queued":
            actions += f"<a href='/cancel/{j['id']}'>cancel</a>"
        job_items.append(
            f"<li><b>#{j['id']}</b> {j['filename']} [{j['status']}] "
            f"<small>{j.get('created_at','')}</small>"
            f"<br/><small>{last_log}</small>"
            f"{f'<br/><code>{err}</code>' if err else ''}"
            f"{preview}<br/>{actions}</li>"
        )
    return f"""<!doctype html>
<html><body style='font-family:Arial,sans-serif;max-width:980px;margin:1.5rem auto'>
<h2>Wildlife Processor</h2>
<p>{msg}</p>
<p>Queue state: <b>{'Paused' if paused else 'Running'}</b> |
<a href='/pause'>Pause</a> | <a href='/resume'>Resume</a> | <a href='/'>Refresh</a></p>
<form method="post" enctype="multipart/form-data" action="/process">
<label>Media file (image/video):</label><br/>
<input type="file" name="media" required /><br/><br/>
<label>Frame rate for video (fps):</label><br/>
<input type="number" step="0.1" value="1" name="fps"/><br/><br/>
<label>ML URL:</label><br/>
<input name="ml_url" value="http://127.0.0.1:8010" style="width:360px"/><br/><br/>
<label>Species URL:</label><br/>
<input name="species_url" value="http://127.0.0.1:8100" style="width:360px"/><br/><br/>
<button type="submit">Queue job</button>
</form>
<h3>Runs</h3>
<ol>{''.join(job_items)}</ol>
</body></html>"""


def _worker_loop() -> None:
    while not _stop_worker:
        time.sleep(1)
        if db.is_paused():
            continue
        job = db.fetch_next_queued()
        if not job:
            continue
        jid = int(job["id"])
        db.mark_running(jid)
        try:
            db.append_log(jid, "Started")
            input_path = Path(job["input_path"])
            out_dir = OUT_DIR / f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_job{jid}"
            out_dir.mkdir(parents=True, exist_ok=True)
            if job["media_type"] == "video":
                db.append_log(jid, "Extracting frames")
                images = extract_frames(input_path, IN_DIR, fps=max(0.1, float(job["fps"])))
                db.append_log(jid, f"Frames: {len(images)}")
            else:
                images = [input_path]
            rows = process_images(
                images,
                out_dir,
                ml_url=job.get("ml_url") or "http://127.0.0.1:8010",
                species_url=job.get("species_url") or "http://127.0.0.1:8100",
            )
            db.append_log(jid, f"Done: {len(rows)} outputs")
            db.mark_done(jid, str(out_dir), rows)
        except Exception as e:
            db.mark_error(jid, str(e))


@app.on_event("startup")
async def startup_event() -> None:
    threading.Thread(target=_worker_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _render_page()


@app.post("/process", response_class=HTMLResponse)
async def process(
    media: UploadFile = File(...),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> HTMLResponse:
    IN_DIR.mkdir(parents=True, exist_ok=True)
    VID_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not media.filename:
        return HTMLResponse(_render_page("Missing filename"))
    suffix = Path(media.filename).suffix.lower()
    if suffix not in SUPPORTED_IMAGES and suffix not in SUPPORTED_VIDEOS:
        return HTMLResponse(_render_page(f"Unsupported type: {suffix}"))
    media_type = "video" if suffix in SUPPORTED_VIDEOS else "image"
    saved = (VID_DIR if media_type == "video" else IN_DIR) / media.filename
    with saved.open("wb") as f:
        shutil.copyfileobj(media.file, f)
    db.add_job(
        filename=media.filename,
        media_type=media_type,
        input_path=str(saved),
        fps=max(0.1, float(fps)),
        ml_url=ml_url,
        species_url=species_url,
    )
    return RedirectResponse(url="/", status_code=303)


@app.get("/pause")
async def pause() -> RedirectResponse:
    db.set_paused(True)
    return RedirectResponse(url="/", status_code=303)


@app.get("/resume")
async def resume() -> RedirectResponse:
    db.set_paused(False)
    return RedirectResponse(url="/", status_code=303)


@app.get("/retry/{job_id}")
async def retry(job_id: int) -> RedirectResponse:
    db.retry_job(job_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/cancel/{job_id}")
async def cancel(job_id: int) -> RedirectResponse:
    db.cancel_job(job_id)
    return RedirectResponse(url="/", status_code=303)
