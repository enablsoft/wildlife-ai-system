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
from urllib.parse import quote_plus, urlparse
from datetime import datetime
from pathlib import Path

from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


def _parse_exts(exts: str) -> set[str]:
    return {x.strip().lower() for x in (exts or "").split(",") if x.strip()}


def _folder_media_index(p: Path, wanted: set[str]) -> dict[str, tuple[Path, str]]:
    idx: dict[str, tuple[Path, str]] = {}
    for f in sorted(p.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in wanted:
            continue
        s = f.suffix.lower()
        if s in SUPPORTED_VIDEOS:
            mt = "video"
        elif s in SUPPORTED_IMAGES:
            mt = "image"
        else:
            continue
        idx[_norm_path(f)] = (f, mt)
    return idx


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


FRAME_RESULTS_PAGE_SIZE = 5
# Recent jobs loaded for Runs tab + frame record extraction (not the Video / Source table).
JOBS_PANEL_LIMIT = 500
SUMMARY_TABLE_PAGE_SIZE = 15


def _aggregate_video_source_summary(
    job_rows: list[dict[str, Any]],
) -> dict[str, dict[str, int | str]]:
    """Roll up jobs by basename(input_path || filename) — same rules as the old in-loop aggregation."""
    video_summary: dict[str, dict[str, int | str]] = {}
    for j in job_rows:
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
        st = str(j.get("status") or "")
        s[st] = int(s.get(st, 0)) + 1
        s["total_frames"] = int(s["total_frames"]) + int(j.get("total_items") or 0)
        s["processed_frames"] = int(s["processed_frames"]) + int(j.get("processed_items") or 0)
    return video_summary


def _home_query(page: int, hide_blanks: bool, summary_page: int) -> str:
    """Query string for home page links (preserve frame + summary pagination)."""
    hb = 1 if hide_blanks else 0
    return f"page={page}&hide_blanks={hb}&summary_page={summary_page}"


def _last_taxon_segment(species: str) -> str:
    """Last non-empty segment of semicolon-separated SpeciesNet labels (e.g. ...;Eastern Gray Squirrel)."""
    for seg in reversed([p.strip() for p in (species or "").split(";")]):
        if seg:
            return seg
    return ""


def _species_string_is_blank(species: str) -> bool:
    """True for __Blank, or when the last taxon segment is Blank (e.g. uuid;;;;;;Blank)."""
    sp = (species or "").lower()
    if "__blank" in sp:
        return True
    return _last_taxon_segment(species or "").lower() == "blank"


def _record_is_blank(species: str, description: str) -> bool:
    """No confident species: __Blank in string, or semicolon path ending in Blank."""
    desc = (description or "").lower()
    if "__blank" in desc:
        return True
    return _species_string_is_blank(species or "")


def _format_species_display(species: str, description: str) -> str:
    if _record_is_blank(species, description):
        return "No species match (blank)"
    return species or "—"


def _render_page(
    msg: str = "",
    page: int = 1,
    hide_blanks: bool = True,
    summary_page: int = 1,
) -> str:
    jobs = db.list_jobs(limit=JOBS_PANEL_LIMIT)
    # Full list for Video Frame Browser (client-side hide/show blanks).
    all_frame_records = _frame_records(jobs)
    records = all_frame_records
    if hide_blanks:
        records = [
            r
            for r in records
            if not _record_is_blank(r["species"], r["description"])
        ]
    page_size = FRAME_RESULTS_PAGE_SIZE
    total_records = len(records)
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size
    page_records = records[start:end]
    paused = db.is_paused()
    # Video / Source List: all jobs in DB (separate query), not limited to JOBS_PANEL_LIMIT.
    video_summary = _aggregate_video_source_summary(db.fetch_all_jobs_for_source_summary())
    sorted_sources = sorted(video_summary.values(), key=lambda x: str(x["source"]).lower())
    total_summary_sources = len(sorted_sources)
    summary_total_pages = max(
        1,
        (total_summary_sources + SUMMARY_TABLE_PAGE_SIZE - 1) // SUMMARY_TABLE_PAGE_SIZE,
    )
    summary_page = max(1, min(summary_page, summary_total_pages))
    ss = (summary_page - 1) * SUMMARY_TABLE_PAGE_SIZE
    se = ss + SUMMARY_TABLE_PAGE_SIZE
    summary_slice = sorted_sources[ss:se]

    job_items: list[str] = []
    counts = {"queued": 0, "running": 0, "done": 0, "error": 0, "cancelled": 0}
    for j in jobs:
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
    summary_pagination_bits: list[str] = []
    if total_summary_sources == 0:
        summary_pagination_bits.append("<span class='job-meta'>0 sources</span>")
    else:
        if summary_page > 1:
            summary_pagination_bits.append(
                f"<a class='link-btn' href='/?{_home_query(page, hide_blanks, summary_page - 1)}'>Prev</a>"
            )
        summary_pagination_bits.append(
            f"<span class='job-meta'>Sources {ss + 1}–{min(se, total_summary_sources)} of "
            f"{total_summary_sources} · Page {summary_page} / {summary_total_pages}</span>"
        )
        if summary_page < summary_total_pages:
            summary_pagination_bits.append(
                f"<a class='link-btn' href='/?{_home_query(page, hide_blanks, summary_page + 1)}'>Next</a>"
            )
    summary_rows = []
    for v in summary_slice:
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
            f"<td>{html.escape(str(v['source']))}</td>"
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
        species_disp = _format_species_display(r["species"], r["description"])
        is_blank = _record_is_blank(r["species"], r["description"])
        search_blob = (
            r["source"]
            + " "
            + r["frame"]
            + " "
            + r["species"]
            + " "
            + species_disp
            + " "
            + r["description"]
            + " blank no species match"
        )
        result_rows.append(
            "<div class='result-card result-row' "
            f"data-is-blank='{'1' if is_blank else '0'}' "
            f"data-search='{html.escape(search_blob.lower(), quote=True)}'>"
            f"<div><a href='/files/{r['annotated_rel']}' target='_blank'>"
            f"<img src='/files/{r['annotated_rel']}' class='thumb' "
            "onerror=\"this.onerror=null;this.replaceWith(document.createTextNode('Image removed'))\"/></a></div>"
            "<div class='result-text'>"
            f"<div><b>Job:</b> #{r['job_id']}</div>"
            f"<div><b>Video:</b> {html.escape(r['source'])}</div>"
            f"<div><b>Frame:</b> {html.escape(r['frame'])}</div>"
            f"<div><b>Species:</b> {html.escape(species_disp)}</div>"
            f"<div class='desc-col' title='{html.escape(r['description'], quote=True)}'>{html.escape(r['description'])}</div>"
            "</div>"
            "</div>"
        )
    pagination_bits: list[str] = []
    if page > 1:
        pagination_bits.append(
            f"<a class='link-btn' href='/?{_home_query(page - 1, hide_blanks, summary_page)}'>Prev</a>"
        )
    pagination_bits.append(
        f"<span class='job-meta'>Page {page} / {total_pages} ({total_records} total, "
        f"{FRAME_RESULTS_PAGE_SIZE} per page)</span>"
    )
    if page < total_pages:
        pagination_bits.append(
            f"<a class='link-btn' href='/?{_home_query(page + 1, hide_blanks, summary_page)}'>Next</a>"
        )
    has_active = counts.get("queued", 0) > 0 or counts.get("running", 0) > 0
    # Always embed all frames so the video browser checkbox can reveal blanks without a full reload.
    records_json = json.dumps(all_frame_records).replace("</", "<\\/")
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
.browser-row{{display:grid;grid-template-columns:minmax(0,280px) minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}}
.browser-row > div{{min-width:0}}
.video-list{{max-height:340px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;box-sizing:border-box}}
.frame-list{{max-height:340px;overflow:auto;border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;box-sizing:border-box}}
.video-item,.frame-item{{display:block;width:100%;text-align:left;padding:7px 8px;border:1px solid #dbe3ef;border-radius:6px;background:#f8fafc;color:#0f172a;cursor:pointer;margin-bottom:6px;box-sizing:border-box;overflow-wrap:anywhere}}
.video-item.active,.frame-item.active{{background:#dbeafe;border-color:#93c5fd}}
.inline-preview{{border:1px solid #e2e8f0;border-radius:8px;padding:8px;background:#fff;display:grid;gap:8px;min-width:0;max-width:100%;max-height:340px;overflow:auto;box-sizing:border-box}}
.inline-preview .job-meta{{overflow-wrap:anywhere;word-break:break-word}}
.inline-preview img{{display:block;width:100%;max-width:100%;height:auto;max-height:min(260px,42vh);object-fit:contain;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}
.tab-btn{{padding:8px 10px;border-radius:8px;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;cursor:pointer}}
.tab-btn.active{{background:#3730a3;color:#fff;border-color:#3730a3}}
.app-modal{{position:fixed;inset:0;z-index:10000;display:none;align-items:center;justify-content:center}}
.app-modal.show{{display:flex}}
.app-modal-backdrop{{position:absolute;inset:0;background:rgba(15,23,42,.55)}}
.app-modal-box{{position:relative;z-index:1;background:#fff;border-radius:12px;padding:18px;max-width:min(720px,94vw);max-height:min(88vh,900px);overflow:auto;box-shadow:0 10px 40px rgba(0,0,0,.2)}}
.app-modal-title{{font-size:18px;font-weight:700;margin-bottom:10px;color:#0f172a}}
.app-modal-body{{margin-bottom:14px;font-size:14px;color:#334155;line-height:1.45}}
.app-modal-actions{{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}}
.enqueue-row{{display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;background:#f8fafc}}
.enqueue-row.disabled{{opacity:.65}}
.enqueue-meta{{font-size:12px;color:#64748b}}
.viewer-overlay{{position:fixed;inset:0;background:rgba(2,6,23,.72);display:none;align-items:center;justify-content:center;z-index:9999}}
.viewer-box{{width:min(96vw,1200px);height:min(92vh,900px);background:#0b1220;border-radius:10px;padding:10px;display:grid;grid-template-rows:auto auto 1fr;gap:8px}}
.viewer-top{{display:flex;justify-content:space-between;align-items:center;color:#e2e8f0}}
.viewer-controls{{display:flex;gap:8px;align-items:center;color:#e2e8f0}}
.viewer-canvas{{overflow:auto;background:#020617;border:1px solid #1e293b;border-radius:8px;display:flex;align-items:flex-start;justify-content:flex-start}}
.viewer-img{{transform-origin:top left;max-width:none;max-height:none}}
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
      <a class='btn btn-subtle js-action' href='/cancel-all' data-confirm='Cancel all queued and running jobs?'>Cancel All</a>
      <a class='btn btn-subtle js-action' href='/clear-jobs' data-confirm='Clear all job records from the database? This cannot be undone.'>Clear Jobs</a>
      <a class='btn btn-subtle js-action' href='/reset-all' data-confirm='Reset everything? This will cancel active jobs, clear job history, and delete generated/local queued files.'>Reset All</a>
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
  <form id='enqueueFolderForm' onsubmit='return false;'>
    <label>Folder Path (local on this machine)</label>
    <input id='enqueueFolderPath' name='folder_path' value='{(VID_DIR).as_posix()}' />
    <div style='height:8px'></div>
    <label>Include extensions (comma-separated)</label>
    <input id='enqueueExts' name='exts' value='.mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp' />
    <div style='height:8px'></div>
    <label>Frame rate (video)</label>
    <input id='enqueueFps' type='number' step='0.1' value='1' name='fps' />
    <div style='height:8px'></div>
    <label>ML URL</label>
    <input name='ml_url' value='http://127.0.0.1:8010' />
    <div style='height:8px'></div>
    <label>Species URL</label>
    <input id='enqueueSpecies' name='species_url' value='http://127.0.0.1:8100' />
    <div style='height:10px'></div>
    <button class='btn' type='button' id='btnEnqueuePreview' onclick='previewEnqueueFolder()'>Preview &amp; queue</button>
    <a class='btn btn-subtle js-action' href='/cleanup-output' data-confirm='Delete all run_* output folders under test-media/output? Active job output folders are skipped.'>Cleanup Output Folder</a>
  </form>
</div>
<div class='panel' style='margin-top:16px'>
  <h3 style='margin-top:0'>Video / Source List</h3>
  <p class='job-meta' style='margin:0 0 10px 0'>Aggregates <b>all</b> jobs in the database by source file (not limited to the recent jobs panel). {SUMMARY_TABLE_PAGE_SIZE} sources per page.</p>
  <div class='actions' style='margin-bottom:10px'>{''.join(summary_pagination_bits)}</div>
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
  <h3 style='margin-top:0'>Video Frame Browser</h3>
  <p class='job-meta' style='margin:0 0 10px 0'>Which frames appear here follows <b>Settings → Hide blank frames</b> (same as Frame Results).</p>
  <div class='browser-row'>
    <div>
      <label>Videos</label>
      <div id='videoList' class='video-list'></div>
    </div>
    <div>
      <label>Frames (selected video)</label>
      <div id='frameList' class='frame-list'></div>
    </div>
    <div>
      <label>Inline Preview</label>
      <div id='inlinePreview' class='inline-preview'>
        <div class='job-meta'>Select a frame to preview</div>
      </div>
    </div>
  </div>
</div>
<div class='tabs'>
  <button id='tabResultsBtn' class='tab-btn active' type='button' onclick='showTab("results")'>Frame Results</button>
  <button id='tabRunsBtn' class='tab-btn' type='button' onclick='showTab("runs")'>Runs</button>
  <button id='tabSettingsBtn' class='tab-btn' type='button' onclick='showTab("settings")'>Settings</button>
</div>
<div id='tabResults' style='display:block'>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Frame Results (Searchable)</h3>
    <p class='job-meta' style='margin:0 0 8px 0'>Up to <b>{FRAME_RESULTS_PAGE_SIZE}</b> rows per page. Blank visibility: <b>Settings</b> tab.</p>
    <label>Search by species, video, frame, or description</label>
    <input id='resultsSearch' placeholder='e.g. hedgehog, IMG_0406, frame_0003, blank' oninput='filterResults()' />
    <div style='height:8px'></div>
    <div class='actions'>{''.join(pagination_bits)}</div>
    <div style='height:10px'></div>
    <div id='resultsBody' class='results-list'>
      {''.join(result_rows) if result_rows else '<div class="job-meta">No processed frames yet</div>'}
    </div>
  </div>
</div>
<div id='tabRuns' style='display:none'>
  <h3>Runs</h3>
  <div class='jobs'>{''.join(job_items)}</div>
</div>
<div id='tabSettings' style='display:none'>
  <div class='panel' style='margin-top:10px'>
    <h3 style='margin-top:0'>Display</h3>
    <p class='job-meta'>One setting for both <b>Frame Results</b> (list + pagination) and <b>Video Frame Browser</b> (frame list + inline preview). Blanks are frames with no species match (label ending in <code>Blank</code> or containing <code>__Blank</code>).</p>
    <label style='font-size:14px;display:flex;align-items:flex-start;gap:10px;cursor:pointer;max-width:52rem'>
      <input type='checkbox' id='settingsHideBlanks' style='width:auto;margin-top:4px' {'checked' if hide_blanks else ''} onchange='applyHideBlanksSetting()' />
      <span><b>Hide blank / no-match frames</b> — when checked, those frames are omitted from Frame Results and from the Video Frame Browser lists.</span>
    </label>
    <p class='job-meta' style='margin-top:14px'>Changing this reloads the page (frame results reset to page 1; summary page is kept).</p>
  </div>
</div>
<div id='appConfirmModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='appConfirmBackdrop'></div>
  <div class='app-modal-box'>
    <div id='appConfirmTitle' class='app-modal-title'>Please confirm</div>
    <div id='appConfirmBody' class='app-modal-body'></div>
    <div class='app-modal-actions'>
      <button type='button' class='btn btn-subtle' id='appConfirmCancel'>Cancel</button>
      <button type='button' class='btn' id='appConfirmOk'>OK</button>
    </div>
  </div>
</div>
<div id='enqueuePreviewModal' class='app-modal' role='dialog' aria-modal='true'>
  <div class='app-modal-backdrop' id='enqueuePreviewBackdrop'></div>
  <div class='app-modal-box' style='max-width:min(860px,96vw)'>
    <div class='app-modal-title'>Review media to queue</div>
    <div id='enqueuePreviewSummary' class='job-meta'></div>
    <div id='enqueuePreviewList'></div>
    <div class='app-modal-actions'>
      <button type='button' class='btn btn-subtle' id='enqueuePreviewCancel'>Cancel</button>
      <button type='button' class='btn' id='enqueuePreviewOk'>Queue selected</button>
    </div>
  </div>
</div>
<div id='viewerOverlay' class='viewer-overlay'>
  <div class='viewer-box'>
    <div class='viewer-top'>
      <div id='viewerTitle'>Image Viewer</div>
      <button class='btn btn-subtle' type='button' onclick='closeViewer()'>Close</button>
    </div>
    <div class='viewer-controls'>
      <span>Zoom</span>
      <input id='zoomRange' type='range' min='20' max='400' value='100' oninput='setViewerZoom(this.value)' />
      <span id='zoomLabel'>100%</span>
      <button class='btn btn-subtle' type='button' onclick='setViewerZoom(100)'>Reset</button>
    </div>
    <div class='viewer-canvas'>
      <img id='viewerImage' class='viewer-img' src='' alt='preview' />
    </div>
  </div>
</div>
<script>
const SCROLL_KEY = 'wildlife_ui_scroll_y';
const HAS_ACTIVE = {"true" if has_active else "false"};
const FRAME_RECORDS = {records_json};
const HIDE_BLANKS = {"true" if hide_blanks else "false"};
let CURRENT_ZOOM = 100;
let ACTIVE_VIDEO = '';
let ACTIVE_FRAME = '';
let _confirmResolve = null;
let _enqueuePreviewState = null;
function lastTaxonSegment(species) {{
  const parts = String(species || '').split(';').map((p) => p.trim());
  for (let i = parts.length - 1; i >= 0; i--) {{
    if (parts[i]) return parts[i];
  }}
  return '';
}}
function isBlankRecord(r) {{
  const s = String(r.species || '');
  const d = String(r.description || '').toLowerCase();
  if (d.includes('__blank')) return true;
  if (s.toLowerCase().includes('__blank')) return true;
  return lastTaxonSegment(s).toLowerCase() === 'blank';
}}
function formatSpeciesLabel(r) {{
  if (!r) return '—';
  if (isBlankRecord(r)) return 'No species match (blank)';
  return r.species || '—';
}}
function openConfirmModal(message) {{
  return new Promise((resolve) => {{
    _confirmResolve = resolve;
    const b = document.getElementById('appConfirmBody');
    if (b) b.textContent = message;
    document.getElementById('appConfirmModal')?.classList.add('show');
  }});
}}
function closeConfirmModal(ok) {{
  document.getElementById('appConfirmModal')?.classList.remove('show');
  if (_confirmResolve) {{
    _confirmResolve(!!ok);
    _confirmResolve = null;
  }}
}}
function closeEnqueuePreview() {{
  document.getElementById('enqueuePreviewModal')?.classList.remove('show');
  _enqueuePreviewState = null;
}}
function esc(s) {{
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');
}}
async function previewEnqueueFolder() {{
  const folder = document.getElementById('enqueueFolderPath')?.value || '';
  const exts = document.getElementById('enqueueExts')?.value || '';
  const res = await fetch('/api/enqueue-folder-preview', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ folder_path: folder, exts: exts }}),
  }});
  const data = await res.json();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Preview failed');
    return;
  }}
  const items = data.items || [];
  if (items.length === 0) {{
    await openConfirmModal('No matching media files in that folder.');
    return;
  }}
  const fps = document.getElementById('enqueueFps')?.value || '1';
  const ml = document.getElementById('enqueueMl')?.value || 'http://127.0.0.1:8010';
  const sp = document.getElementById('enqueueSpecies')?.value || 'http://127.0.0.1:8100';
  _enqueuePreviewState = {{ folder: folder, exts: exts, fps: fps, ml_url: ml, species_url: sp, items: items }};
  const done = items.filter((x) => x.prior_status === 'done').length;
  const active = items.filter((x) => x.prior_status === 'queued' || x.prior_status === 'running').length;
  const sum = document.getElementById('enqueuePreviewSummary');
  if (sum) sum.textContent = `${{items.length}} file(s) found — ${{done}} previously completed, ${{active}} already in queue.`;
  const list = document.getElementById('enqueuePreviewList');
  if (!list) return;
  list.innerHTML = '';
  list.style.maxHeight = '48vh';
  list.style.overflow = 'auto';
  items.forEach((it) => {{
    const st = it.prior_status;
    const row = document.createElement('div');
    row.className = 'enqueue-row' + ((st === 'queued' || st === 'running') ? ' disabled' : '');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.path = it.input_path;
    if (st === 'queued' || st === 'running') {{
      cb.disabled = true;
      cb.checked = false;
    }} else if (st === 'done') {{
      cb.disabled = false;
      cb.checked = false;
    }} else {{
      cb.disabled = false;
      cb.checked = true;
    }}
    const label = document.createElement('label');
    label.style.flex = '1';
    let note = 'new';
    if (st === 'done') note = `processed (job #${{it.prior_job_id || '?'}})`;
    else if (st === 'queued' || st === 'running') note = `in queue (job #${{it.prior_job_id || '?'}})`;
    else if (st) note = `last: ${{st}} (#${{it.prior_job_id || '?'}})`;
    label.innerHTML = `<b>${{esc(it.filename)}}</b> <span class='enqueue-meta'>(${{it.media_type}}) — ${{esc(note)}}</span>`;
    row.appendChild(cb);
    row.appendChild(label);
    list.appendChild(row);
  }});
  document.getElementById('enqueuePreviewModal')?.classList.add('show');
}}
async function commitEnqueuePreview() {{
  if (!_enqueuePreviewState) return;
  const paths = [];
  document.querySelectorAll('#enqueuePreviewList input[type=checkbox]').forEach((cb) => {{
    if (!cb.disabled && cb.checked && cb.dataset.path) paths.push(cb.dataset.path);
  }});
  if (paths.length === 0) {{
    await openConfirmModal('Select at least one file to queue (enable checkbox for completed items to re-run).');
    return;
  }}
  const body = {{
    folder_path: _enqueuePreviewState.folder,
    exts: _enqueuePreviewState.exts,
    fps: Number(_enqueuePreviewState.fps) || 1,
    ml_url: _enqueuePreviewState.ml_url,
    species_url: _enqueuePreviewState.species_url,
    input_paths: paths,
  }};
  const res = await fetch('/api/enqueue-folder-commit', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body),
  }});
  const data = await res.json();
  closeEnqueuePreview();
  if (!data.ok) {{
    await openConfirmModal(data.error || 'Queue failed');
    return;
  }}
  const u = new URL(window.location.origin + '/');
  u.searchParams.set('msg', data.message || 'Queued.');
  window.location.href = u.toString();
}}
(function initModals() {{
  document.getElementById('appConfirmOk')?.addEventListener('click', () => closeConfirmModal(true));
  document.getElementById('appConfirmCancel')?.addEventListener('click', () => closeConfirmModal(false));
  document.getElementById('appConfirmBackdrop')?.addEventListener('click', () => closeConfirmModal(false));
  document.getElementById('enqueuePreviewCancel')?.addEventListener('click', () => closeEnqueuePreview());
  document.getElementById('enqueuePreviewBackdrop')?.addEventListener('click', () => closeEnqueuePreview());
}})();
window.addEventListener('beforeunload', () => {{
  sessionStorage.setItem(SCROLL_KEY, String(window.scrollY || 0));
}});
window.addEventListener('load', () => {{
  const u = new URL(window.location.href);
  if (u.searchParams.has('msg')) {{
    u.searchParams.delete('msg');
    window.history.replaceState(null, '', u.pathname + (u.search ? u.search : ''));
  }}
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
    const confirmMsg = el.getAttribute('data-confirm');
    if (confirmMsg) {{
      const ok = await openConfirmModal(confirmMsg);
      if (!ok) return;
    }}
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
function openViewer(src, title) {{
  const ov = document.getElementById('viewerOverlay');
  const img = document.getElementById('viewerImage');
  const ttl = document.getElementById('viewerTitle');
  if (!ov || !img || !ttl) return;
  img.src = src;
  ttl.textContent = title || 'Image Viewer';
  setViewerZoom(100);
  ov.style.display = 'flex';
}}
function closeViewer() {{
  const ov = document.getElementById('viewerOverlay');
  if (ov) ov.style.display = 'none';
}}
function setViewerZoom(v) {{
  const z = Math.max(20, Math.min(400, Number(v) || 100));
  CURRENT_ZOOM = z;
  const img = document.getElementById('viewerImage');
  const lbl = document.getElementById('zoomLabel');
  const rng = document.getElementById('zoomRange');
  if (img) img.style.transform = `scale(${{z / 100}})`;
  if (lbl) lbl.textContent = `${{z}}%`;
  if (rng && String(rng.value) !== String(z)) rng.value = String(z);
}}
function showTab(name) {{
  const isResults = name === 'results';
  const isRuns = name === 'runs';
  const isSettings = name === 'settings';
  document.getElementById('tabResults').style.display = isResults ? 'block' : 'none';
  document.getElementById('tabRuns').style.display = isRuns ? 'block' : 'none';
  const ts = document.getElementById('tabSettings');
  if (ts) ts.style.display = isSettings ? 'block' : 'none';
  document.getElementById('tabResultsBtn')?.classList.toggle('active', isResults);
  document.getElementById('tabRunsBtn')?.classList.toggle('active', isRuns);
  document.getElementById('tabSettingsBtn')?.classList.toggle('active', isSettings);
}}
function applyHideBlanksSetting() {{
  const cb = document.getElementById('settingsHideBlanks');
  if (!cb) return;
  const u = new URL(window.location.href);
  u.searchParams.set('hide_blanks', cb.checked ? '1' : '0');
  u.searchParams.set('page', '1');
  window.location.href = u.toString();
}}
function attachImageClickHandlers() {{
  document.querySelectorAll('#resultsBody img.thumb').forEach((img) => {{
    const src = img.getAttribute('src') || '';
    const card = img.closest('.result-card');
    const title = card ? (card.querySelector('.result-text')?.textContent || 'Frame') : 'Frame';
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', (e) => {{
      e.preventDefault();
      openViewer(src, title);
    }});
    const a = img.closest('a');
    if (a) a.addEventListener('click', (e) => e.preventDefault());
  }});
}}
function renderInlinePreview(r) {{
  const box = document.getElementById('inlinePreview');
  if (!box) return;
  if (!r) {{
    box.innerHTML = "<div class='job-meta'>Select a frame to preview</div>";
    return;
  }}
  const src = `/files/${{r.annotated_rel}}`;
  const sp = esc(formatSpeciesLabel(r));
  box.innerHTML = `
    <div><b>${{esc(r.source)}}</b></div>
    <div class='job-meta'>${{esc(r.frame)}} | ${{sp}}</div>
    <img src="${{src}}" alt="frame preview" loading="lazy" />
    <div class='job-meta'>${{esc(r.description || '')}}</div>
    <div><button class='btn btn-subtle' type='button' onclick="openViewer('${{src}}','${{(r.source + ' :: ' + r.frame).replace(/'/g, "\\\\'")}}')">Open Zoom Viewer</button></div>
  `;
}}
function renderVideoBrowser() {{
  const hideBlanks = HIDE_BLANKS;
  const sourceRecords = hideBlanks
    ? FRAME_RECORDS.filter((r) => !isBlankRecord(r))
    : FRAME_RECORDS.slice();
  const videoMap = new Map();
  for (const r of sourceRecords) {{
    const k = r.source || 'unknown';
    if (!videoMap.has(k)) videoMap.set(k, []);
    videoMap.get(k).push(r);
  }}
  const videoNames = Array.from(videoMap.keys())
    .filter((n) => (videoMap.get(n) || []).length > 0)
    .sort((a, b) => a.localeCompare(b));
  const vList = document.getElementById('videoList');
  const fList = document.getElementById('frameList');
  const prevBox = document.getElementById('inlinePreview');
  if (!vList || !fList) return;
  if (videoNames.length === 0) {{
    vList.innerHTML = "<div class='job-meta'>No videos with visible frames" + (hideBlanks ? " — <b>Settings</b>: turn off &quot;Hide blank…&quot; if every frame is blank." : "") + "</div>";
    fList.innerHTML = "<div class='job-meta'>No frames to show</div>";
    if (prevBox) prevBox.innerHTML = "<div class='job-meta'>No frame to preview</div>";
    return;
  }}
  if (!ACTIVE_VIDEO || !videoMap.has(ACTIVE_VIDEO)) ACTIVE_VIDEO = videoNames[0];
  vList.innerHTML = videoNames.map((name) => {{
    const active = name === ACTIVE_VIDEO ? ' active' : '';
    const cnt = videoMap.get(name).length;
    return `<button class='video-item${{active}}' type='button' data-video='${{esc(name)}}'>${{esc(name)}} (${{cnt}})</button>`;
  }}).join('');
  vList.querySelectorAll('.video-item').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      ACTIVE_VIDEO = btn.getAttribute('data-video') || '';
      ACTIVE_FRAME = '';
      renderVideoBrowser();
    }});
  }});
  const frames = (videoMap.get(ACTIVE_VIDEO) || []).slice().sort((a, b) => String(a.frame).localeCompare(String(b.frame)));
  if (!ACTIVE_FRAME && frames.length > 0) ACTIVE_FRAME = String(frames[0].annotated_rel);
  if (frames.length > 0 && !frames.some((x) => String(x.annotated_rel) === ACTIVE_FRAME)) {{
    ACTIVE_FRAME = String(frames[0].annotated_rel);
  }}
  fList.innerHTML = frames.map((r) => {{
    const src = `/files/${{r.annotated_rel}}`;
    const active = String(r.annotated_rel) === ACTIVE_FRAME ? ' active' : '';
    const text = `${{esc(String(r.frame))}} | ${{esc(formatSpeciesLabel(r))}}`;
    return `<button class='frame-item${{active}}' type='button' data-src='${{src}}' data-id='${{esc(String(r.annotated_rel))}}' data-title='${{esc((r.source || '') + ' :: ' + (r.frame || ''))}}'>${{text}}</button>`;
  }}).join('');
  fList.querySelectorAll('.frame-item').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      ACTIVE_FRAME = btn.getAttribute('data-id') || '';
      const picked = frames.find((x) => String(x.annotated_rel) === ACTIVE_FRAME);
      renderInlinePreview(picked || null);
      renderVideoBrowser();
    }});
  }});
  const first = frames.find((x) => String(x.annotated_rel) === ACTIVE_FRAME) || frames[0];
  renderInlinePreview(first || null);
}}
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
document.getElementById('viewerOverlay')?.addEventListener('click', (e) => {{
  if (e.target && e.target.id === 'viewerOverlay') closeViewer();
}});
function filterResults(){{
  const q = (document.getElementById('resultsSearch')?.value || '').toLowerCase().trim();
  const rows = document.querySelectorAll('.result-row');
  rows.forEach((row)=>{{
    const text = (row.getAttribute('data-search') || '').toLowerCase();
    const matchQ = !q || text.includes(q);
    row.style.display = matchQ ? '' : 'none';
  }});
}}
attachImageClickHandlers();
renderVideoBrowser();
setTimeout(() => {{
  const picker = document.getElementById('multiFiles');
  const hasPickerFiles = !!(picker && picker.files && picker.files.length > 0);
  const hasDropped = !!(window._droppedFiles && window._droppedFiles.length > 0);
  const isTyping = document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA');
  if (HAS_ACTIVE && !hasPickerFiles && !hasDropped && !isTyping) {{
    window.location.reload();
  }}
}}, 3000)
</script>
</div></body></html>"""


class EnqueueFolderPreviewIn(BaseModel):
    folder_path: str
    exts: str = ".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp"


class EnqueueFolderCommitIn(BaseModel):
    folder_path: str
    exts: str = ".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp"
    fps: float = 1.0
    ml_url: str = "http://127.0.0.1:8010"
    species_url: str = "http://127.0.0.1:8100"
    input_paths: list[str] = Field(default_factory=list)


@app.post("/api/enqueue-folder-preview")
async def api_enqueue_folder_preview(body: EnqueueFolderPreviewIn) -> JSONResponse:
    raw = (body.folder_path or "").strip().strip('"').strip("'")
    p = Path(raw)
    if not p.is_dir():
        return JSONResponse({"ok": False, "error": f"Folder not found: {raw}"}, status_code=400)
    wanted = _parse_exts(body.exts)
    try:
        idx = _folder_media_index(p, wanted)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    items: list[dict[str, Any]] = []
    for norm, (fp, mt) in sorted(idx.items(), key=lambda kv: kv[1][0].name.lower()):
        prior = db.latest_job_for_input(norm, mt)
        pst = str(prior["status"]) if prior else None
        pid = int(prior["id"]) if prior else None
        items.append(
            {
                "filename": fp.name,
                "media_type": mt,
                "input_path": norm,
                "prior_status": pst,
                "prior_job_id": pid,
            }
        )
    return JSONResponse({"ok": True, "folder": raw, "items": items})


@app.post("/api/enqueue-folder-commit")
async def api_enqueue_folder_commit(body: EnqueueFolderCommitIn) -> JSONResponse:
    raw = (body.folder_path or "").strip().strip('"').strip("'")
    p = Path(raw)
    if not p.is_dir():
        return JSONResponse({"ok": False, "error": f"Folder not found: {raw}"}, status_code=400)
    wanted = _parse_exts(body.exts)
    try:
        idx = _folder_media_index(p, wanted)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    q = 0
    skipped = 0
    missing = 0
    for norm in body.input_paths:
        if norm not in idx:
            missing += 1
            continue
        f, mt = idx[norm]
        jid = db.add_job(
            filename=f.name,
            media_type=mt,
            input_path=norm,
            fps=max(0.1, float(body.fps)),
            ml_url=body.ml_url,
            species_url=body.species_url,
        )
        if jid < 0:
            skipped += 1
        else:
            q += 1
    logger.info("batch_enqueued_commit count=%s skipped=%s missing=%s folder=%s", q, skipped, missing, raw)
    msg = f"Queued {q} file(s), skipped {skipped} active duplicate(s), {missing} path(s) not in folder."
    return JSONResponse({"ok": True, "queued": q, "skipped": skipped, "missing": missing, "message": msg})


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
async def index(
    msg: str = "",
    page: int = 1,
    hide_blanks: int = Query(1, ge=0, le=1),
    summary_page: int = Query(1, ge=1),
) -> str:
    return _render_page(
        msg,
        page=page,
        hide_blanks=bool(hide_blanks),
        summary_page=summary_page,
    )


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
    logger.info("process_multi received files=%s", len(media_files))
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


def _same_origin_referer_or(request: Request, default: str = "/") -> str:
    """Use Referer for post-action redirect if it matches this app (avoid losing ?page= etc.)."""
    ref = (request.headers.get("referer") or "").strip()
    if not ref:
        return default
    try:
        r = urlparse(ref)
        b = urlparse(str(request.base_url))
        if r.scheme == b.scheme and r.netloc == b.netloc and r.path:
            return ref
    except Exception:
        pass
    return default


@app.get("/open-output/{job_id}")
async def open_output(job_id: int, request: Request) -> RedirectResponse:
    j = db.get_job(job_id)
    if j and j.get("output_dir"):
        p = Path(j["output_dir"])
        if p.is_dir():
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif os.name == "posix":
                subprocess.Popen(["xdg-open", str(p)])
    return RedirectResponse(url=_same_origin_referer_or(request), status_code=303)


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
    page_html = (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;max-width:980px;margin:20px auto'>"
        f"<h3>Output Browser - Job #{job_id}</h3>"
        f"<p>Folder: <code>{rel_dir}</code></p>"
        "<p><a href='/'>Back</a></p>"
        + "".join(rows)
        + "</body></html>"
    )
    return HTMLResponse(page_html)


@app.get("/cleanup-output")
async def cleanup_output() -> RedirectResponse:
    active_dirs: set[str] = set()
    for j in db.list_jobs(limit=JOBS_PANEL_LIMIT):
        if j.get("status") in ("queued", "running") and j.get("output_dir"):
            active_dirs.add(str(j.get("output_dir")))
    if OUT_DIR.is_dir():
        for d in OUT_DIR.glob("run_*"):
            if d.is_dir() and str(d) not in active_dirs:
                shutil.rmtree(d, ignore_errors=True)
    logger.info("cleanup_output done")
    return RedirectResponse(url="/", status_code=303)
