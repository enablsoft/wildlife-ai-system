from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse

from webapp.pipeline import SUPPORTED_IMAGES, SUPPORTED_VIDEOS, extract_frames, process_images

ROOT = Path(__file__).resolve().parents[1]
TEST_MEDIA = ROOT / "test-media"
IN_DIR = TEST_MEDIA / "input"
VID_DIR = TEST_MEDIA / "video"
OUT_DIR = TEST_MEDIA / "output"

app = FastAPI(title="Wildlife Media Processor", version="0.1.0")


def _html(msg: str, extra: str = "") -> str:
    return f"""<!doctype html>
<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:2rem auto'>
<h2>Wildlife Processor</h2>
<p>{msg}</p>
<form method="post" enctype="multipart/form-data" action="/process">
<label>Media file (image/video):</label><br/>
<input type="file" name="media" required /><br/><br/>
<label>Frame rate for video (fps):</label><br/>
<input type="number" step="0.1" value="1" name="fps"/><br/><br/>
<button type="submit">Process</button>
</form>
{extra}
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _html("Upload a file to run detector + species + annotation.")


@app.post("/process", response_class=HTMLResponse)
async def process(
    media: UploadFile = File(...),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> str:
    IN_DIR.mkdir(parents=True, exist_ok=True)
    VID_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not media.filename:
        return _html("Missing file name.")
    suffix = Path(media.filename).suffix.lower()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_out = OUT_DIR / f"run_{ts}"
    run_out.mkdir(parents=True, exist_ok=True)

    saved = (VID_DIR if suffix in SUPPORTED_VIDEOS else IN_DIR) / media.filename
    with saved.open("wb") as f:
        shutil.copyfileobj(media.file, f)

    if suffix in SUPPORTED_VIDEOS:
        try:
            images = extract_frames(saved, IN_DIR, fps=max(0.1, float(fps)))
        except Exception as e:
            return _html(f"Video frame extraction failed: {e}")
    elif suffix in SUPPORTED_IMAGES:
        images = [saved]
    else:
        return _html(f"Unsupported file type: {suffix}")

    try:
        rows = process_images(images, run_out, ml_url=ml_url, species_url=species_url)
    except Exception as e:
        return _html(f"Processing failed: {e}")

    items = "".join(
        f"<li>{Path(r['annotated']).name} | "
        f"<code>{Path(r['ml_json']).name}</code> | "
        f"<code>{Path(r['species_json']).name}</code></li>"
        for r in rows
    )
    extra = f"<p>Output folder: <code>{run_out}</code></p><ul>{items}</ul>"
    return _html(f"Processed {len(rows)} image(s).", extra)
