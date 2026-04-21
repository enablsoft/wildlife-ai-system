from __future__ import annotations

import json
import logging
import os
import html
import re
import subprocess
import shutil
import threading
import time
from urllib.parse import quote_plus
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
logger = logging.getLogger("wildlife_webapp")
if not logger.handlers:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _safe_rel(path_str: str) -> str:
    return Path(path_str).relative_to(ROOT).as_posix()


def _norm_path(p: Path) -> str:
    try:
        return str(p.resolve()).lower()
    except Exception:
        return str(p).lower()


def _enqueue_uploaded_file(
    media: UploadFile,
    fps: float,
    ml_url: str,
    species_url: str,
) -> tuple[int, str]:
    if not media.filename:
        return (0, "Missing filename")
    suffix = Path(media.filename).suffix.lower()
    if suffix not in SUPPORTED_IMAGES and suffix not in SUPPORTED_VIDEOS:
        return (0, f"Unsupported type: {suffix}")
    media_type = "video" if suffix in SUPPORTED_VIDEOS else "image"
    IN_DIR.mkdir(parents=True, exist_ok=True)
    VID_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = (VID_DIR if media_type == "video" else IN_DIR) / Path(media.filename).name
    with saved.open("wb") as f:
        shutil.copyfileobj(media.file, f)
    jid = db.add_job(
        filename=Path(media.filename).name,
        media_type=media_type,
        input_path=_norm_path(saved),
        fps=max(0.1, float(fps)),
        ml_url=ml_url,
        species_url=species_url,
    )
    if jid < 0:
        return (jid, f"Already exists as job #{abs(jid)} for file: {Path(media.filename).name}")
    logger.info(
        "job_queued file=%s media_type=%s ml_url=%s species_url=%s",
        media.filename,
        media_type,
        ml_url,
        species_url,
    )
    return (jid, "")


def _clean_species(value: str | None) -> str:
    if not value:
        return "Unknown"
    return value.replace("_", " ").strip().title()


def _frame_records(jobs: list[dict[str, object]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for j in jobs:
        if j.get("status") != "done" or not j.get("outputs_json"):
            continue
        source = Path(str(j.get("input_path") or j.get("filename") or "")).name
        try:
            outputs = json.loads(str(j["outputs_json"]))
        except Exception:
            continue
        if not isinstance(outputs, list):
            continue
        for row in outputs:
            if not isinstance(row, dict):
                continue
            ann = str(row.get("annotated") or "")
            frame_name = Path(str(row.get("input") or "")).name
            species = "Unknown"
            species_conf = ""
            det_class = "Unknown"
            det_conf = ""
            sp_path = Path(str(row.get("species_json") or ""))
            ml_path = Path(str(row.get("ml_json") or ""))
            if sp_path.is_file():
                try:
                    sp = json.loads(sp_path.read_text(encoding="utf-8"))
                    species = _clean_species(str(sp.get("prediction") or "Unknown"))
                    conf = sp.get("score")
                    if isinstance(conf, (float, int)):
                        species_conf = f"{float(conf):.2f}"
                except Exception:
                    pass
            if ml_path.is_file():
                try:
                    det = json.loads(ml_path.read_text(encoding="utf-8"))
                    objs = det.get("objects") or []
                    if objs and isinstance(objs, list):
                        top = objs[0]
                        if isinstance(top, dict):
                            det_class = str(top.get("class") or "Unknown")
                            c = top.get("confidence")
                            if isinstance(c, (float, int)):
                                det_conf = f"{float(c):.2f}"
                except Exception:
                    pass
            desc = (
                f"Likely {species}"
                + (f" ({species_conf})" if species_conf else "")
                + f" in {source}, frame {frame_name}. "
                + f"Detector: {det_class}"
                + (f" ({det_conf})" if det_conf else "")
                + "."
            )
            try:
                ann_rel = _safe_rel(ann)
            except Exception:
                continue
            records.append(
                {
                    "job_id": str(j.get("id") or ""),
                    "source": source,
                    "frame": frame_name,
                    "species": species,
                    "description": desc,
                    "annotated_rel": ann_rel,
                }
            )
    return records


def _render_page(msg: str = "", page: int = 1) -> str:
    jobs = db.list_jobs(limit=200)
    records = _frame_records(jobs)
    page_size = 5
    total_records = len(records)
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size
    page_records = records[start:end]
    paused = db.is_paused()
    video_summary: dict[str, dict[str, int | str]] = {}
    job_items: list[str] = []
    counts = {"queued": 0, "running": 0, "done": 0, "error": 0, "cancelled": 0}
    for j in jobs:
        source = Path(j.get("input_path") or j.get("filename") or "").name
        key = source
        if key not in video_summary:
            video_summary[key] = {
                "source": source,
                "queued": 0,
                "running": 0,
                "done": 0,
                "error": 0,
                "cancelled": 0,
                "total_frames": 0,
                "processed_frames": 0,
            }
        s = video_summary[key]
        s[j["status"]] = int(s.get(j["status"], 0)) + 1
        s["total_frames"] = int(s["total_frames"]) + int(j.get("total_items") or 0)
        s["processed_frames"] = int(s["processed_frames"]) + int(j.get("processed_items") or 0)
        counts[j["status"]] = counts.get(j["status"], 0) + 1
        preview = ""
        if j.get("outputs_json"):
            try:
                outputs = json.loads(j["outputs_json"])
                if outputs:
                    ann = _safe_rel(outputs[0]["annotated"])
                    preview = (
                        f"<img src='/files/{ann}' class='preview' "
                        "onerror=\"this.onerror=null;this.replaceWith(document.createTextNode('Preview not available (file removed)'))\"/>"
                    )
            except Exception:
                preview = ""
        logs = (j.get("logs") or "").strip().splitlines()
        last_log = logs[-1] if logs else ""
        err = j.get("error_text") or ""
        actions = ""
        if j["status"] in ("error", "cancelled"):
            actions += f"<a class='link-btn js-action' href='/retry/{j['id']}'>Retry</a> "
        if j["status"] == "queued":
            actions += f"<a class='link-btn js-action' href='/cancel/{j['id']}'>Cancel</a>"
        status_class = {
            "queued": "st-queued",
            "running": "st-running",
            "done": "st-done",
            "error": "st-error",
            "cancelled": "st-cancelled",
        }.get(j["status"], "")
        out_dir = j.get("output_dir") or ""
        out_link = ""
        open_link = ""
        if out_dir:
            try:
                rel = Path(out_dir).relative_to(ROOT).as_posix()
                out_link = f"<a class='link-btn' href='/browse-output/{j['id']}'>Output Browser</a>"
                open_link = f"<a class='link-btn' href='/open-output/{j['id']}'>Open Folder</a>"
            except Exception:
                out_link = ""
                open_link = ""
        prog = ""
        total = int(j.get("total_items") or 0)
        done_n = int(j.get("processed_items") or 0)
        if total > 0:
            pct = int((done_n / total) * 100)
            prog = f"<div class='progress'><div class='bar' style='width:{pct}%'></div></div><div class='job-meta'>Progress: {done_n}/{total}</div>"
        job_items.append(
            f"<div class='job-card'>"
            f"<div class='job-head'><div><b>#{j['id']}</b> {j['filename']}</div>"
            f"<span class='status {status_class}'>{j['status']}</span></div>"
            f"<div class='job-meta'>Created: {j.get('created_at','')} | Started: {j.get('started_at') or '-'} | Finished: {j.get('finished_at') or '-'}</div>"
            f"{prog}"
            f"<div class='job-log'>{last_log or '-'}</div>"
            f"{f'<div class=\"job-err\">{err}</div>' if err else ''}"
            f"{preview}"
            f"<div class='job-actions'>{actions} {out_link} {open_link}</div>"
            f"</div>"
        )
    summary_rows = []
    for v in sorted(video_summary.values(), key=lambda x: str(x["source"]).lower()):
        total = int(v["total_frames"])
        proc = int(v["processed_frames"])
        pct = int((proc / total) * 100) if total > 0 else 0
        overall = (
            "error"
            if int(v["error"]) > 0
            else "running"
            if int(v["running"]) > 0
            else "queued"
            if int(v["queued"]) > 0
            else "done"
            if int(v["done"]) > 0
            else "cancelled"
        )
        summary_rows.append(
            f"<tr>"
            f"<td>{v['source']}</td>"
            f"<td>{overall}</td>"
            f"<td>{v['queued']}</td>"
            f"<td>{v['running']}</td>"
            f"<td>{v['done']}</td>"
            f"<td>{v['error']}</td>"
            f"<td>{v['cancelled']}</td>"
            f"<td>{proc}/{total} ({pct}%)</td>"
            f"</tr>"
        )
    result_rows: list[str] = []
    for r in page_records:
        result_rows.append(
            "<div class='result-card result-row' "
            f"data-search='{html.escape((r['source'] + ' ' + r['frame'] + ' ' + r['species'] + ' ' + r['description']).lower(), quote=True)}'>"
            f"<div><a href='/files/{r['annotated_rel']}' target='_blank'>"
            f"<img src='/files/{r['annotated_rel']}' class='thumb' "
            "onerror=\"this.onerror=null;this.replaceWith(document.createTextNode('Image removed'))\"/></a></div>"
            "<div class='result-text'>"
            f"<div><b>Job:</b> #{r['job_id']}</div>"
            f"<div><b>Video:</b> {html.escape(r['source'])}</div>"
            f"<div><b>Frame:</b> {html.escape(r['frame'])}</div>"
            f"<div><b>Species:</b> {html.escape(r['species'])}</div>"
            f"<div class='desc-col' title='{html.escape(r['description'], quote=True)}'>{html.escape(r['description'])}</div>"
            "</div>"
            "</div>"
        )
    pagination_bits: list[str] = []
    if page > 1:
        pagination_bits.append(f"<a class='link-btn' href='/?page={page - 1}'>Prev</a>")
    pagination_bits.append(f"<span class='job-meta'>Page {page} / {total_pages} ({total_records} total)</span>")
    if page < total_pages:
        pagination_bits.append(f"<a class='link-btn' href='/?page={page + 1}'>Next</a>")
    return f"""<!doctype html>
<html><head><meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>Wildlife Processor</title>
<style>
body{{font-family:Inter,Segoe UI,Arial,sans-serif;background:#f6f8fb;color:#1e293b;margin:0}}
.wrap{{max-width:1200px;margin:0 auto;padding:20px}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.title{{font-size:28px;font-weight:700}}
.badge{{padding:6px 10px;border-radius:999px;background:{'#fef3c7' if paused else '#dcfce7'};color:#111827;font-weight:600}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.panel{{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.counts{{display:grid;grid-template-columns:repeat(5,minmax(80px,1fr));gap:8px;margin-top:8px}}
.count{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px;text-align:center}}
.count b{{display:block;font-size:20px}}
label{{font-size:13px;font-weight:600;color:#334155}}
input{{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box}}
.btn{{display:inline-block;padding:9px 12px;border-radius:8px;background:#0f172a;color:white;text-decoration:none;border:0;cursor:pointer}}
.btn-subtle{{background:#334155}}
.actions{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.actions a{{margin-right:0}}
.msg{{margin:8px 0;color:#0f766e}}
.jobs{{margin-top:14px;display:grid;grid-template-columns:1fr;gap:12px}}
.job-card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:12px}}
.job-head{{display:flex;justify-content:space-between;align-items:center}}
.status{{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase}}
.st-queued{{background:#e2e8f0}} .st-running{{background:#bfdbfe}} .st-done{{background:#bbf7d0}} .st-error{{background:#fecaca}} .st-cancelled{{background:#f1f5f9}}
.job-meta{{font-size:12px;color:#64748b;margin-top:4px}}
.job-log{{margin-top:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px;font-family:Consolas,monospace;font-size:12px}}
.job-err{{margin-top:8px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:8px;color:#991b1b;font-family:Consolas,monospace;font-size:12px}}
.preview{{margin-top:10px;max-width:360px;border:1px solid #cbd5e1;border-radius:8px}}
.job-actions{{margin-top:10px}}
.link-btn{{display:inline-block;padding:6px 10px;background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;border-radius:8px;text-decoration:none;margin-right:8px}}
.progress{{height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin-top:8px}}
.bar{{height:100%;background:#3b82f6}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px}}
.tbl th,.tbl td{{border:1px solid #e2e8f0;padding:8px;text-align:left}}
.tbl th{{background:#f8fafc}}
.thumb{{max-width:220px;border:1px solid #cbd5e1;border-radius:6px}}
.results-list{{display:grid;gap:10px}}
.result-card{{display:grid;grid-template-columns:240px 1fr;gap:12px;align-items:start;padding:10px;border:1px solid #e2e8f0;border-radius:10px;background:#fff}}
.result-text{{display:grid;gap:5px;font-size:13px}}
.desc-col{{max-width:100%;color:#334155}}
</style></head>
<body><div class='wrap'>
<div class='top'><div class='title'>Wildlife Processor</div><div class='badge'>{'Paused' if paused else 'Running'}</div></div>
<div class='msg'>{msg}</div>
<div class='row'>
  <div class='panel'>
    <h3 style='margin-top:0'>Queue Control</h3>
    <div class='actions'>
      <a class='btn btn-subtle js-action' href='/pause'>Pause</a>
      <a class='btn btn-subtle js-action' href='/resume'>Resume</a>
      <a class='btn btn-subtle js-action' href='/cancel-all'>Cancel All</a>
      <a class='btn btn-subtle js-action' href='/clear-jobs'>Clear Jobs</a>
      <a class='btn btn-subtle js-action' href='/reset-all'>Reset All</a>
      <a class='btn btn-subtle' href='/' >Refresh</a>
    </div>
    <div class='counts'>
      <div class='count'><small>Queued</small><b>{counts.get('queued',0)}</b></div>
      <div class='count'><small>Running</small><b>{counts.get('running',0)}</b></div>
      <div class='count'><small>Done</small><b>{counts.get('done',0)}</b></div>
      <div class='count'><small>Error</small><b>{counts.get('error',0)}</b></div>
      <div class='count'><small>Cancelled</small><b>{counts.get('cancelled',0)}</b></div>
    </div>
  </div>
  <div class='panel'>
    <h3 style='margin-top:0'>New Job</h3>
    <form method='post' enctype='multipart/form-data' action='/process'>
      <label>Media file (image/video)</label><input type='file' name='media' required />
      <div style='height:8px'></div>
      <label>Frame rate (video)</label><input type='number' step='0.1' value='1' name='fps'/>
      <div style='height:8px'></div>
      <label>ML URL</label><input name='ml_url' value='http://127.0.0.1:8010'/>
      <div style='height:8px'></div>
      <label>Species URL</label><input name='species_url' value='http://127.0.0.1:8100'/>
      <div style='height:10px'></div>
      <button class='btn' type='submit'>Queue Job</button>
    </form>
    <div style='height:10px'></div>
    <h4 style='margin:6px 0'>Quick Batch Upload (files/folder/drag-drop)</h4>
    <input id='multiFiles' type='file' multiple webkitdirectory />
    <div style='height:8px'></div>
    <div id='dropZone' style='border:2px dashed #94a3b8;border-radius:10px;padding:12px;color:#334155'>
      Drag & drop files here
    </div>
    <div style='height:8px'></div>
    <button class='btn' type='button' onclick='queueSelectedFiles()'>Queue Selected Files</button>
  </div>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Batch Queue From Folder</h3>
  <form method='post' action='/enqueue-folder'>
    <label>Folder Path (local on this machine)</label>
    <input name='folder_path' value='{(VID_DIR).as_posix()}' />
    <div style='height:8px'></div>
    <label>Include extensions (comma-separated)</label>
    <input name='exts' value='.mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp' />
    <div style='height:8px'></div>
    <label>Frame rate (video)</label>
    <input type='number' step='0.1' value='1' name='fps' />
    <div style='height:8px'></div>
    <label>ML URL</label>
    <input name='ml_url' value='http://127.0.0.1:8010' />
    <div style='height:8px'></div>
    <label>Species URL</label>
    <input name='species_url' value='http://127.0.0.1:8100' />
    <div style='height:10px'></div>
    <button class='btn' type='submit'>Enqueue Folder</button>
    <a class='btn btn-subtle js-action' href='/cleanup-output'>Cleanup Output Folder</a>
  </form>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Video / Source List</h3>
  <table class='tbl'>
    <thead>
      <tr><th>Source</th><th>Overall</th><th>Queued</th><th>Running</th><th>Done</th><th>Error</th><th>Cancelled</th><th>Frame Progress</th></tr>
    </thead>
    <tbody>
      {''.join(summary_rows) if summary_rows else '<tr><td colspan="8">No sources yet</td></tr>'}
    </tbody>
  </table>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Frame Results (Searchable)</h3>
  <label>Search by species, video, frame, or description</label>
  <input id='resultsSearch' placeholder='e.g. hedgehog, IMG_0406, frame_0003' oninput='filterResults()' />
  <div style='height:8px'></div>
  <div class='actions'>{''.join(pagination_bits)}</div>
  <div style='height:10px'></div>
  <div id='resultsBody' class='results-list'>
    {''.join(result_rows) if result_rows else '<div class="job-meta">No processed frames yet</div>'}
  </div>
</div>
<h3>Runs</h3>
<div class='jobs'>{''.join(job_items)}</div>
<script>
const SCROLL_KEY = 'wildlife_ui_scroll_y';
window.addEventListener('beforeunload', () => {{
  sessionStorage.setItem(SCROLL_KEY, String(window.scrollY || 0));
}});
window.addEventListener('load', () => {{
  const y = Number(sessionStorage.getItem(SCROLL_KEY) || '0');
  if (Number.isFinite(y) && y > 0) {{
    window.scrollTo({{ top: y, behavior: 'auto' }});
  }}
}});
document.querySelectorAll('a.js-action').forEach((el) => {{
  el.addEventListener('click', async (evt) => {{
    evt.preventDefault();
    const href = el.getAttribute('href');
    if (!href) return;
    sessionStorage.setItem(SCROLL_KEY, String(window.scrollY || 0));
    try {{
      await fetch(href, {{ method: 'GET', credentials: 'same-origin' }});
    }} catch (_) {{
      // Fall back to regular navigation if request fails.
      window.location.href = href;
      return;
    }}
    window.location.reload();
  }});
}});
async function queueSelectedFiles() {{
  const picker = document.getElementById('multiFiles');
  const files = (window._droppedFiles && window._droppedFiles.length) ? window._droppedFiles : (picker?.files || []);
  if (!files || files.length === 0) {{
    alert('Select files (or a folder) first.');
    return;
  }}
  const ml = document.querySelector("input[name='ml_url']")?.value || 'http://127.0.0.1:8010';
  const sp = document.querySelector("input[name='species_url']")?.value || 'http://127.0.0.1:8100';
  const fps = document.querySelector("input[name='fps']")?.value || '1';
  const fd = new FormData();
  for (const f of files) fd.append('media_files', f);
  fd.append('ml_url', ml);
  fd.append('species_url', sp);
  fd.append('fps', fps);
  const res = await fetch('/process-multi', {{ method: 'POST', body: fd }});
  if (res.redirected) {{
    window.location.href = res.url;
    return;
  }}
  window.location.reload();
}}
const dz = document.getElementById('dropZone');
if (dz) {{
  dz.addEventListener('dragover', (e) => {{
    e.preventDefault();
    dz.style.borderColor = '#3b82f6';
  }});
  dz.addEventListener('dragleave', () => {{
    dz.style.borderColor = '#94a3b8';
  }});
  dz.addEventListener('drop', (e) => {{
    e.preventDefault();
    dz.style.borderColor = '#94a3b8';
    const files = Array.from(e.dataTransfer?.files || []);
    window._droppedFiles = files;
    dz.textContent = files.length ? `${{files.length}} file(s) ready` : 'Drag & drop files here';
  }});
}}
function filterResults(){{
  const q = (document.getElementById('resultsSearch')?.value || '').toLowerCase().trim();
  const rows = document.querySelectorAll('.result-row');
  rows.forEach((row)=>{{
    const text = row.getAttribute('data-search') || '';
    row.style.display = (!q || text.includes(q)) ? '' : 'none';
  }});
}}
setTimeout(()=>location.reload(),3000)
</script>
</div></body></html>"""


def _worker_loop() -> None:
    logger.info("Worker loop started")
    while not _stop_worker:
        time.sleep(1)
        if db.is_paused():
            continue
        job = db.fetch_next_queued()
        if not job:
            continue
        jid = int(job["id"])
        logger.info("job_id=%s status=running file=%s", jid, job["filename"])
        db.mark_running(jid)
        try:
            db.append_log(jid, "Started")
            input_path = Path(job["input_path"])
            out_dir = OUT_DIR / f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_job{jid}"
            out_dir.mkdir(parents=True, exist_ok=True)
            if job["media_type"] == "video":
                db.append_log(jid, "Extracting frames")
                logger.info("job_id=%s stage=extract_frames input=%s", jid, input_path.name)
                images = extract_frames(input_path, IN_DIR, fps=max(0.1, float(job["fps"])))
                db.append_log(jid, f"Frames: {len(images)}")
                logger.info("job_id=%s stage=extract_frames_done frames=%s", jid, len(images))
            else:
                images = [input_path]
                logger.info("job_id=%s stage=image_ready file=%s", jid, input_path.name)
            db.set_total_items(jid, len(images))
            logger.info("job_id=%s stage=inference count=%s", jid, len(images))
            rows = process_images(
                images,
                out_dir,
                ml_url=job.get("ml_url") or "http://127.0.0.1:8010",
                species_url=job.get("species_url") or "http://127.0.0.1:8100",
                progress_cb=lambda n, t, p: db.set_processed_items(jid, n),
                should_continue_cb=lambda: not db.is_cancelled(jid),
            )
            if db.is_cancelled(jid):
                db.append_log(jid, "Cancelled by user")
                logger.info("job_id=%s status=cancelled", jid)
                continue
            db.append_log(jid, f"Done: {len(rows)} outputs")
            db.mark_done(jid, str(out_dir), rows)
            logger.info("job_id=%s status=done outputs=%s out_dir=%s", jid, len(rows), out_dir)
        except Exception as e:
            if db.is_cancelled(jid):
                db.append_log(jid, "Cancelled by user")
                logger.info("job_id=%s status=cancelled", jid)
            else:
                db.mark_error(jid, str(e))
                logger.exception("job_id=%s status=error msg=%s", jid, e)


@app.on_event("startup")
async def startup_event() -> None:
    threading.Thread(target=_worker_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index(msg: str = "", page: int = 1) -> str:
    return _render_page(msg, page=page)


@app.post("/process", response_class=HTMLResponse)
async def process(
    media: UploadFile = File(...),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> HTMLResponse:
    jid, err = _enqueue_uploaded_file(media, fps=fps, ml_url=ml_url, species_url=species_url)
    if err:
        return RedirectResponse(url=f"/?msg={quote_plus(err)}", status_code=303)
    if jid < 0:
        return RedirectResponse(
            url=f"/?msg={quote_plus(f'Already exists as job #{abs(jid)} for file: {media.filename}. Clear jobs or retry existing.')}",
            status_code=303,
        )
    return RedirectResponse(url="/", status_code=303)


@app.post("/process-multi")
async def process_multi(
    media_files: list[UploadFile] = File(...),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> RedirectResponse:
    queued = 0
    skipped = 0
    bad = 0
    for media in media_files:
        jid, err = _enqueue_uploaded_file(media, fps=fps, ml_url=ml_url, species_url=species_url)
        if err:
            bad += 1
            continue
        if jid < 0:
            skipped += 1
        else:
            queued += 1
    return RedirectResponse(
        url=f"/?msg={quote_plus(f'Queued {queued} file(s), skipped {skipped}, unsupported/missing {bad}.')}",
        status_code=303,
    )


@app.get("/pause")
async def pause() -> RedirectResponse:
    db.set_paused(True)
    logger.info("queue_state=paused")
    return RedirectResponse(url="/", status_code=303)


@app.get("/resume")
async def resume() -> RedirectResponse:
    db.set_paused(False)
    logger.info("queue_state=running")
    return RedirectResponse(url="/", status_code=303)


@app.get("/retry/{job_id}")
async def retry(job_id: int) -> RedirectResponse:
    db.retry_job(job_id)
    logger.info("job_id=%s action=retry", job_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/cancel/{job_id}")
async def cancel(job_id: int) -> RedirectResponse:
    db.cancel_job(job_id)
    logger.info("job_id=%s action=cancel", job_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/enqueue-folder")
async def enqueue_folder(
    folder_path: str = Form(...),
    exts: str = Form(".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp"),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> HTMLResponse:
    p = Path(folder_path)
    if not p.is_dir():
        return RedirectResponse(url=f"/?msg={quote_plus(f'Folder not found: {folder_path}')}", status_code=303)
    wanted = {x.strip().lower() for x in exts.split(",") if x.strip()}
    files = [f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix.lower() in wanted]
    if not files:
        return RedirectResponse(url=f"/?msg={quote_plus(f'No matching files in {folder_path}')}", status_code=303)
    q = 0
    skipped = 0
    for f in files:
        s = f.suffix.lower()
        if s in SUPPORTED_VIDEOS:
            mt = "video"
        elif s in SUPPORTED_IMAGES:
            mt = "image"
        else:
            continue
        jid = db.add_job(
            filename=f.name,
            media_type=mt,
            input_path=_norm_path(f),
            fps=max(0.1, float(fps)),
            ml_url=ml_url,
            species_url=species_url,
        )
        if jid < 0:
            skipped += 1
        else:
            q += 1
    logger.info("batch_enqueued count=%s skipped=%s folder=%s", q, skipped, folder_path)
    return RedirectResponse(
        url=f"/?msg={quote_plus(f'Batch queued {q} file(s), skipped {skipped} existing file(s) from {folder_path}.')}",
        status_code=303,
    )


@app.get("/cancel-all")
async def cancel_all() -> RedirectResponse:
    n = db.cancel_all_active()
    logger.info("cancel_all affected=%s", n)
    return RedirectResponse(url=f"/?msg={quote_plus(f'Cancelled {n} active job(s).')}", status_code=303)


@app.get("/clear-jobs")
async def clear_jobs() -> RedirectResponse:
    if db.has_running_jobs():
        return RedirectResponse(
            url=f"/?msg={quote_plus('Cannot clear while a job is running. Cancel all first and wait a moment.')}",
            status_code=303,
        )
    n = db.clear_all_jobs()
    logger.info("clear_jobs removed=%s", n)
    return RedirectResponse(url=f"/?msg={quote_plus(f'Cleared {n} job record(s).')}", status_code=303)


@app.get("/reset-all")
async def reset_all() -> RedirectResponse:
    cancelled = db.cancel_all_active()
    if OUT_DIR.is_dir():
        for d in OUT_DIR.glob("run_*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    if IN_DIR.is_dir():
        for f in IN_DIR.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception:
                    pass
    if VID_DIR.is_dir():
        for f in VID_DIR.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception:
                    pass
    removed = db.clear_all_jobs()
    logger.info("reset_all cancelled=%s cleared=%s", cancelled, removed)
    return RedirectResponse(
        url=f"/?msg={quote_plus(f'Reset complete. Cancelled {cancelled} active job(s), cleared {removed} jobs, and removed generated files.')}",
        status_code=303,
    )


@app.get("/open-output/{job_id}")
async def open_output(job_id: int) -> RedirectResponse:
    j = db.get_job(job_id)
    if j and j.get("output_dir"):
        p = Path(j["output_dir"])
        if p.is_dir():
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", str(p)])
    return RedirectResponse(url="/", status_code=303)


@app.get("/browse-output/{job_id}", response_class=HTMLResponse)
async def browse_output(job_id: int) -> HTMLResponse:
    j = db.get_job(job_id)
    if not j or not j.get("output_dir"):
        return HTMLResponse("<p>Output folder not available. <a href='/'>Back</a></p>")
    p = Path(j["output_dir"])
    if not p.is_dir():
        return HTMLResponse("<p>Output folder does not exist on disk. <a href='/'>Back</a></p>")
    try:
        rel_dir = p.relative_to(ROOT).as_posix()
    except Exception:
        return HTMLResponse("<p>Output folder is outside app root. <a href='/'>Back</a></p>")

    rows: list[str] = []
    ann_files = sorted(
        [f for f in p.iterdir() if f.is_file() and f.name.lower().endswith(".annotated.jpg")],
        key=lambda x: x.name.lower(),
    )
    for ann in ann_files:
        base = re.sub(r"\.annotated\.jpg$", "", ann.name, flags=re.IGNORECASE)
        species = "Unknown"
        species_conf = ""
        det_class = "Unknown"
        det_conf = ""
        sp_path = p / f"{base}.species.json"
        ml_path = p / f"{base}.ml.json"
        if sp_path.is_file():
            try:
                sp = json.loads(sp_path.read_text(encoding="utf-8"))
                species = _clean_species(str(sp.get("prediction") or "Unknown"))
                conf = sp.get("score")
                if isinstance(conf, (float, int)):
                    species_conf = f"{float(conf):.2f}"
            except Exception:
                pass
        if ml_path.is_file():
            try:
                det = json.loads(ml_path.read_text(encoding="utf-8"))
                objs = det.get("objects") or []
                if objs and isinstance(objs, list):
                    top = objs[0]
                    if isinstance(top, dict):
                        det_class = str(top.get("class") or "Unknown")
                        c = top.get("confidence")
                        if isinstance(c, (float, int)):
                            det_conf = f"{float(c):.2f}"
            except Exception:
                pass
        rel = ann.relative_to(ROOT).as_posix()
        desc = (
            f"Likely {species}"
            + (f" ({species_conf})" if species_conf else "")
            + f" in frame {base}. Detector: {det_class}"
            + (f" ({det_conf})" if det_conf else "")
            + "."
        )
        rows.append(
            "<div style='margin-bottom:20px;padding:12px;border:1px solid #e2e8f0;border-radius:8px'>"
            f"<div><b>Frame:</b> {html.escape(base)}</div>"
            f"<div><b>Species:</b> {html.escape(species)}</div>"
            f"<div style='margin:6px 0 10px 0;color:#334155'>{html.escape(desc)}</div>"
            f"<a href='/files/{rel}' target='_blank'>{html.escape(ann.name)}</a><br/>"
            f"<img src='/files/{rel}' style='max-width:860px;border:1px solid #ddd;border-radius:6px'/>"
            "</div>"
        )
    if not rows:
        rows.append("<p>No annotated frames found in this output folder.</p>")
    html = (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;max-width:980px;margin:20px auto'>"
        f"<h3>Output Browser - Job #{job_id}</h3>"
        f"<p>Folder: <code>{rel_dir}</code></p>"
        "<p><a href='/'>Back</a></p>"
        + "".join(rows)
        + "</body></html>"
    )
    return HTMLResponse(html)


@app.get("/cleanup-output")
async def cleanup_output() -> RedirectResponse:
    active_dirs: set[str] = set()
    for j in db.list_jobs(limit=500):
        if j.get("status") in ("queued", "running") and j.get("output_dir"):
            active_dirs.add(str(j.get("output_dir")))
    if OUT_DIR.is_dir():
        for d in OUT_DIR.glob("run_*"):
            if d.is_dir() and str(d) not in active_dirs:
                shutil.rmtree(d, ignore_errors=True)
    logger.info("cleanup_output done")
    return RedirectResponse(url="/", status_code=303)
