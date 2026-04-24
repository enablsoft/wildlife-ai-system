from __future__ import annotations

"""
Wildlife webapp bootstrap and orchestration layer.

Function index (quick search):
- _render_page: prepare home-page data and delegate HTML rendering.
- _worker_loop: run background queue processing loop.
- home/process/process_multi: serve UI and queue media jobs.
- pause/resume/cancel/reset routes: control queue and retention actions.
"""

import json
import logging
import logging.handlers
import os
import html
import re
import subprocess
import shutil
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from pathlib import Path

from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageEnhance, ImageOps

from webapp.export_utils import (
    export_frames_xlsx,
    format_species_display,
    record_is_blank,
    species_string_is_blank,
)
from webapp.jobs_db import create_jobs_db
from webapp.pipeline import SUPPORTED_IMAGES, SUPPORTED_VIDEOS, extract_frames, process_images
from webapp.routes_api import register_api_routes
from webapp.runtime_paths import defaults, runtime_dirs, validate_runtime_dir
from webapp.ui_render import render_home_page_html, render_output_browser_page
from webapp.worker import run_worker_loop

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "webapp_jobs.sqlite"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "webapp.log"

db = create_jobs_db(DB_PATH)
_stop_worker = False
_trailcam_overlay_cache: dict[str, dict[str, str]] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _normalize_persisted_species_labels_once()
    threading.Thread(target=_worker_loop, daemon=True).start()
    yield


app = FastAPI(title="Wildlife Media Processor", version="0.2.0", lifespan=lifespan)
app.mount("/files", StaticFiles(directory=str(ROOT)), name="files")
logger = logging.getLogger("wildlife_webapp")
if not logger.handlers:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    rotate_when = (os.environ.get("LOG_ROTATE_WHEN") or "midnight").strip() or "midnight"
    try:
        rotate_interval = max(1, int(os.environ.get("LOG_ROTATE_INTERVAL", "1")))
    except ValueError:
        rotate_interval = 1
    try:
        backup_days = max(1, int(os.environ.get("LOG_BACKUP_DAYS", "14")))
    except ValueError:
        backup_days = 14
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotating = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE,
        when=rotate_when,
        interval=rotate_interval,
        backupCount=backup_days,
        encoding="utf-8",
    )
    stream = logging.StreamHandler()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[stream, rotating],
    )


def _safe_rel(path_str: str) -> str:
    return Path(path_str).relative_to(ROOT).as_posix()


def _norm_path(p: Path) -> str:
    """Resolved absolute path; preserve casing for case-sensitive filesystems and ffmpeg."""
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def _safe_log_value(value: Any) -> Any:
    """Strip control characters from untrusted log fields."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return re.sub(r"[\r\n\t]+", " ", str(value))


def _parse_exts(exts: str) -> set[str]:
    return {x.strip().lower() for x in (exts or "").split(",") if x.strip()}


def _runtime_dirs() -> tuple[Path, Path, Path]:
    return runtime_dirs(ROOT, db)


def _ensure_dir_gitkeep(folder: Path) -> None:
    """Ensure folder exists and keeps a .gitkeep placeholder."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    except Exception:
        # Cleanup/reset should remain best-effort even if filesystem write fails.
        pass


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
    input_dir, video_dir, output_dir = _runtime_dirs()
    input_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = (video_dir if media_type == "video" else input_dir) / Path(media.filename).name
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
        "job_queued file=%s media_type=%s",
        _safe_log_value(media.filename),
        media_type,
    )
    return (jid, "")


def _clean_species(value: str | None) -> str:
    if not value:
        return "Unknown"
    def _is_no_cv_token(token: str) -> bool:
        t = token.strip().lower().replace("_", " ")
        t = " ".join(t.split())
        return t in {
            "no cv result",
            "nocvresult",
            "no cv",
            "no result",
            "no species",
            "none",
            "n/a",
        }
    raw = value.replace("_", " ").strip()
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if parts and all(_is_no_cv_token(p) for p in parts):
        return "__Blank"
    if _is_no_cv_token(raw):
        return "__Blank"
    # Some classifiers prefix taxonomy with a UUID-like dataset/model key.
    # Keep user-facing species labels readable by dropping that leading token.
    if parts:
        first = parts[0].strip().strip("'\"{}[]()")
        if re.fullmatch(r"[0-9a-fA-F]{32}", first) or re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            first,
        ):
            parts = parts[1:]
    cleaned = ";".join(parts) if parts else raw
    return cleaned.title()


def _normalize_persisted_species_labels_once() -> None:
    """One-time cleanup: strip UUID prefixes from historical species JSON predictions."""
    if db.get_control("species_label_migration_v1", "0") == "1":
        return
    scanned = 0
    updated = 0
    for j in db.list_all_jobs():
        raw_outputs = j.get("outputs_json")
        if not raw_outputs:
            continue
        try:
            outputs = json.loads(str(raw_outputs))
        except Exception:
            continue
        if not isinstance(outputs, list):
            continue
        for row in outputs:
            if not isinstance(row, dict):
                continue
            sp_path = Path(str(row.get("species_json") or ""))
            if not sp_path.is_file():
                continue
            scanned += 1
            try:
                payload = json.loads(sp_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                pred = str(payload.get("prediction") or "")
                cleaned = _clean_species(pred)
                if cleaned and cleaned != pred:
                    payload["prediction"] = cleaned
                    sp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    updated += 1
            except Exception:
                continue
    db.set_control("species_label_migration_v1", "1")
    logger.info("species_label_migration_v1 scanned=%s updated=%s", scanned, updated)


def _species_short_name(species: str) -> str:
    parts = [p.strip() for p in (species or "").split(";") if p.strip()]
    if parts:
        return parts[-1].replace("_", " ").strip().title()
    return _clean_species(species)


def _species_latin_name(species: str) -> str:
    parts = [p.strip() for p in (species or "").split(";") if p.strip()]
    if parts and " " in parts[-1]:
        if len(parts) >= 6:
            # Taxonomy layout is commonly:
            # class;order;family;genus;species;common_name
            genus = parts[-3].replace("_", " ").strip().title()
            epithet = parts[-2].replace("_", " ").strip().title()
            return f"{genus} {epithet}".strip()
        # If only a common-name tail is present (missing species slot), keep genus only.
        if len(parts) >= 2:
            return parts[-2].replace("_", " ").strip().title()
    if len(parts) >= 6:
        genus = parts[-3].replace("_", " ").strip().title()
        epithet = parts[-2].replace("_", " ").strip().title()
        return f"{genus} {epithet}".strip()
    if len(parts) >= 2:
        genus = parts[-2].replace("_", " ").strip().title()
        epithet = parts[-1].replace("_", " ").strip().title()
        return f"{genus} {epithet}".strip()
    if len(parts) == 1:
        return parts[-1].replace("_", " ").strip().title()
    return ""


def _species_type_tag(species: str) -> str:
    s = (species or "").lower()
    if not s or "__blank" in s or "no cv result" in s:
        return ""
    if species_string_is_blank(species or ""):
        return ""
    checks = [
        ("bird", ["aves", "bird"]),
        ("mammal", ["mammalia", "mammal"]),
        ("reptile", ["reptilia", "reptile"]),
        ("amphibian", ["amphibia", "amphib"]),
        ("fish", ["actinopterygii", "fish"]),
        ("insect", ["insecta", "insect"]),
        ("arachnid", ["arachnida", "arachnid", "spider"]),
    ]
    for tag, keys in checks:
        if any(k in s for k in keys):
            return tag
    return "animal"


def _extract_species_candidates(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract top species candidates as (label, confidence_text)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    min_score = 0.02

    def _push(label_raw: Any, score_raw: Any, *, allow_blank_label: bool = False) -> None:
        label = _clean_species(str(label_raw or "").strip())
        if not label or label.lower() == "unknown":
            return
        if (not allow_blank_label) and species_string_is_blank(label):
            return
        key = label.lower()
        if key in seen:
            return
        seen.add(key)
        conf = ""
        if isinstance(score_raw, (float, int)):
            if float(score_raw) < min_score and not allow_blank_label:
                return
            conf = f"{float(score_raw):.2f}"
        out.append((label, conf))

    _push(payload.get("prediction"), payload.get("score"), allow_blank_label=True)
    _push(payload.get("prediction"), payload.get("prediction_score"), allow_blank_label=True)
    _push(payload.get("prediction"), payload.get("confidence"), allow_blank_label=True)

    for key in ("predictions", "candidates", "top_predictions", "results", "classes"):
        v = payload.get(key)
        if not isinstance(v, list):
            continue
        for it in v:
            if not isinstance(it, dict):
                continue
            label_raw = (
                it.get("prediction")
                or it.get("label")
                or it.get("species")
                or it.get("name")
                or it.get("class")
                or ""
            )
            score_raw = it.get("score")
            if not isinstance(score_raw, (float, int)):
                score_raw = it.get("confidence")
            if not isinstance(score_raw, (float, int)):
                score_raw = it.get("probability")
            _push(label_raw, score_raw)
            if len(out) >= 5:
                return out
    # SpeciesNet-style payloads often carry rich candidates under raw.classifications.
    raw = payload.get("raw")
    if isinstance(raw, dict):
        cls = raw.get("classifications")
        if isinstance(cls, dict):
            classes = cls.get("classes")
            scores = cls.get("scores")
            if isinstance(classes, list):
                for idx, label_raw in enumerate(classes):
                    score_raw: Any = ""
                    if isinstance(scores, list) and idx < len(scores):
                        score_raw = scores[idx]
                    _push(label_raw, score_raw)
                    if len(out) >= 5:
                        return out
    return out[:5]


def _extract_detector_objects(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract detector objects as (class, confidence_text)."""
    objs = payload.get("objects")
    if not isinstance(objs, list):
        return []
    out: list[tuple[str, str]] = []
    for it in objs:
        if not isinstance(it, dict):
            continue
        cls = str(it.get("class") or "").replace("_", " ").strip()
        if not cls:
            continue
        conf = ""
        c = it.get("confidence")
        if isinstance(c, (float, int)):
            conf = f"{float(c):.2f}"
        out.append((cls, conf))
        if len(out) >= 8:
            break
    return out


def _refine_species_for_display(
    species: str,
    species_conf: str,
    species_candidates: list[tuple[str, str]],
    detector_objects: list[tuple[str, str]],
) -> tuple[str, str, list[tuple[str, str]]]:
    """Reduce misleading labels for generic/no-box frames."""
    if detector_objects:
        return species, species_conf, species_candidates
    if species_string_is_blank(species or "") or str(species or "").strip().lower() in ("unknown", "__blank"):
        return species, species_conf, species_candidates
    primary_tag = _species_type_tag(species)
    if primary_tag not in ("", "animal", "mammal"):
        return species, species_conf, species_candidates

    bird_candidates = [(label, conf) for label, conf in species_candidates if _species_type_tag(label) == "bird"]
    if not bird_candidates:
        return species, species_conf, species_candidates

    top_bird_label, top_bird_conf = bird_candidates[0]
    filtered: list[tuple[str, str]] = [(species, species_conf)] if species else []
    filtered.extend(bird_candidates[:4])
    return top_bird_label, top_bird_conf or species_conf, filtered[:5]


def _normalize_tags_csv(raw: str) -> str:
    parts = [p.strip() for p in (raw or "").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return ", ".join(out)


def _resolve_tesseract_bin() -> str | None:
    candidates = [
        shutil.which("tesseract") or "",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _extract_trailcam_overlay_fields(image_path: Path) -> dict[str, str]:
    key = str(image_path)
    if key in _trailcam_overlay_cache:
        return _trailcam_overlay_cache[key]
    out = {"overlay_temp": "", "overlay_date": "", "overlay_time": ""}
    if not image_path.is_file():
        _trailcam_overlay_cache[key] = out
        return out
    tesseract_bin = _resolve_tesseract_bin()
    if not tesseract_bin:
        _trailcam_overlay_cache[key] = out
        return out
    def _normalize_mmddyy(date_raw: str) -> str:
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2,4})", (date_raw or "").strip())
        if not m:
            return ""
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            y = int(yy)
            yy = f"20{y:02d}" if y <= 79 else f"19{y:02d}"
        return f"{mm}/{dd}/{yy}"

    def _best_date_from_text(text: str) -> str:
        blob = re.sub(r"\s+", " ", (text or "").replace("\n", " "))
        four = list(re.finditer(r"\b(\d{2})/(\d{2})/(\d{4})\b", blob))
        if four:

            def _score_date(m: re.Match[str]) -> tuple[int, str]:
                raw = m.group(0)
                norm = _normalize_mmddyy(raw)
                try:
                    yyyy = int(norm.split("/")[2])
                except Exception:
                    yyyy = 0
                pri = 0
                if 2015 <= yyyy <= 2035:
                    pri += 5
                if raw.count("/") == 2 and len(m.group(3)) == 4:
                    pri += 2
                return (pri, norm)

            scored = sorted((_score_date(m) for m in four), key=lambda x: x[0], reverse=True)
            return scored[0][1] if scored else _normalize_mmddyy(four[0].group(0))
        # OCR sometimes merges separators into an 8-digit run like "03192025" (often near the time).
        tim = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", blob, flags=re.IGNORECASE)
        if tim:
            window = blob[max(0, tim.start() - 40) : min(len(blob), tim.end() + 10)]
            digits = re.sub(r"\D+", "", window)
            cands: list[str] = []
            pos = 0
            while pos + 8 <= len(digits):
                chunk = digits[pos : pos + 8]
                mm, dd, yyyy = chunk[0:2], chunk[2:4], chunk[4:8]
                if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31 and int(yyyy) >= 1990:
                    cands.append(_normalize_mmddyy(f"{mm}/{dd}/{yyyy}"))
                pos += 1
            if cands:
                def _y(s: str) -> int:
                    try:
                        return int(s.split("/")[2])
                    except Exception:
                        return 0

                cands.sort(key=lambda s: abs(_y(s) - 2026))
                return cands[0]
        two = list(re.finditer(r"\b(\d{2})/(\d{2})/(\d{2})\b", blob))
        if not two:
            return ""
        # Prefer a 2-digit year that also appears as a 4-digit year nearby (common OCR split).
        for m in two:
            mm, dd, tail = m.group(1), m.group(2), m.group(3)
            window = blob[max(0, m.start() - 22) : m.end() + 22]
            m4 = re.search(rf"\b{re.escape(mm)}/{re.escape(dd)}/(20{re.escape(tail)}|19{re.escape(tail)})\b", window)
            if m4:
                return _normalize_mmddyy(m4.group(0))
            if tail == "20":
                m5 = re.search(rf"\b{re.escape(mm)}/{re.escape(dd)}/(20\d{{2}})\b", window)
                if m5:
                    return _normalize_mmddyy(m5.group(0))
        return _normalize_mmddyy(two[0].group(0))

    def _best_temp_from_text(text: str) -> str:
        blob = re.sub(r"\s+", " ", (text or "").replace("\n", " "))
        scored: list[tuple[int, str]] = []
        for m in re.finditer(r"\b(-?\d{1,3})\s*°?\s*([CF])\b", blob, flags=re.IGNORECASE):
            try:
                v = int(m.group(1))
            except Exception:
                continue
            letter = m.group(2).upper()
            if letter != "C" or not (-55 <= v <= 60):
                continue
            pri = 0
            if 0 <= v <= 45:
                pri += 2
            # Prefer temps that appear near a date stamp in the same OCR blob.
            if re.search(r"\d{2}/\d{2}/\d{2,4}", blob[max(0, m.start() - 30) : m.end() + 30]):
                pri += 4
            scored.append((pri, -abs(v), f"{v}{letter}"))
        if not scored:
            return ""
        scored.sort(key=lambda x: (x[0], x[1]))
        return scored[-1][2]

    def _ocr_footer_png(footer_bin: Image.Image) -> str:
        tmp = image_path.with_suffix(".ocr_tmp.png")
        try:
            footer_bin.save(tmp)
            chunks: list[str] = []
            for psm in ("6", "11", "12"):
                cp = subprocess.run(
                    [tesseract_bin, str(tmp), "stdout", "--psm", psm],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    check=False,
                )
                if cp.returncode == 0 and (cp.stdout or "").strip():
                    chunks.append(cp.stdout)
            return "\n".join(chunks)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("trailcam_overlay_tmp_cleanup_failed path=%s err=%s", tmp, exc)

    try:
        with Image.open(image_path).convert("RGB") as im:
            w, h = im.size
            texts: list[str] = []
            for frac in (0.83, 0.80):
                y0 = max(0, int(h * frac))
                footer = im.crop((0, y0, w, h)).convert("L")
                footer = ImageEnhance.Contrast(footer).enhance(2.8)
                footer = ImageEnhance.Sharpness(footer).enhance(2.0)
                footer = ImageOps.autocontrast(footer)
                inv = ImageOps.invert(footer)
                for base in (footer, inv):
                    bw = base.point(lambda p: 255 if p > 128 else 0)
                    texts.append(_ocr_footer_png(bw))
                    bw2 = base.point(lambda p: 255 if p > 112 else 0)
                    texts.append(_ocr_footer_png(bw2))
                    try:
                        big = bw.resize((max(1, bw.width * 2), max(1, bw.height * 2)))
                        texts.append(_ocr_footer_png(big))
                    except Exception:
                        pass
            text = "\n".join(t for t in texts if t)
        out["overlay_temp"] = _best_temp_from_text(text)
        if not out["overlay_temp"]:
            tmatch = re.search(r"\b(-?\d{1,2})\s*([CF])\b", text, flags=re.IGNORECASE)
            if tmatch:
                out["overlay_temp"] = f"{tmatch.group(1)}{tmatch.group(2).upper()}"
            else:
                tmatch2 = re.search(r"\b(-?\d{1,2})0\b", text)
                if tmatch2:
                    out["overlay_temp"] = f"{tmatch2.group(1)}C"
        out["overlay_date"] = _best_date_from_text(text)
        timematch = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", text, flags=re.IGNORECASE)
        if timematch:
            out["overlay_time"] = re.sub(r"\s+", "", timematch.group(1).upper())
    except Exception as exc:
        logger.debug("trailcam_overlay_ocr_failed image=%s err=%s", image_path, exc)
    _trailcam_overlay_cache[key] = out
    return out


def _frame_records(jobs: list[dict[str, object]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen_ann: set[str] = set()

    def _append_record(source: str, row: dict[str, object], job_id: object) -> None:
        ann = str(row.get("annotated") or "")
        frame_name = Path(str(row.get("input") or "")).name
        species = "Unknown"
        species_conf = ""
        det_class = "Unknown"
        det_conf = ""
        species_candidates: list[tuple[str, str]] = []
        detector_objects: list[tuple[str, str]] = []
        sp_path = Path(str(row.get("species_json") or ""))
        ml_path = Path(str(row.get("ml_json") or ""))
        if sp_path.is_file():
            try:
                sp = json.loads(sp_path.read_text(encoding="utf-8"))
                if isinstance(sp, dict):
                    species_candidates = _extract_species_candidates(sp)
                species = _clean_species(str(sp.get("prediction") or "Unknown"))
                conf = sp.get("score")
                if not isinstance(conf, (float, int)):
                    conf = sp.get("prediction_score")
                if not isinstance(conf, (float, int)):
                    conf = sp.get("confidence")
                if isinstance(conf, (float, int)):
                    species_conf = f"{float(conf):.2f}"
            except Exception as exc:
                logger.debug("species_json_read_failed path=%s err=%s", sp_path, exc)
        if ml_path.is_file():
            try:
                det = json.loads(ml_path.read_text(encoding="utf-8"))
                if isinstance(det, dict):
                    detector_objects = _extract_detector_objects(det)
                objs = det.get("objects") or []
                if objs and isinstance(objs, list):
                    top = objs[0]
                    if isinstance(top, dict):
                        det_class = str(top.get("class") or "Unknown")
                        c = top.get("confidence")
                        if isinstance(c, (float, int)):
                            det_conf = f"{float(c):.2f}"
            except Exception as exc:
                logger.debug("ml_json_read_failed path=%s err=%s", ml_path, exc)
        species, species_conf, species_candidates = _refine_species_for_display(
            species,
            species_conf,
            species_candidates,
            detector_objects,
        )
        species_short = _species_short_name(species)
        species_latin = _species_latin_name(species)
        if not species_candidates and species_short and species_short.lower() != "unknown":
            species_candidates = [(species_short, species_conf)]
        desc = (
            f"Likely {species_short}" + (f" ({species_latin})" if species_latin else "")
            + (f" ({species_conf})" if species_conf else "")
            + f" in {source}, frame {frame_name}. "
            + f"Detector: {det_class}"
            + (f" ({det_conf})" if det_conf else "")
            + "."
        )
        try:
            ann_rel = _safe_rel(ann)
        except Exception:
            return
        if ann_rel in seen_ann:
            return
        seen_ann.add(ann_rel)
        records.append(
            {
                "job_id": str(job_id or ""),
                "source": source,
                "frame": frame_name,
                "input_abs": str(row.get("input") or ""),
                "species": species,
                "description": desc,
                "species_confidence": species_conf,
                "detector_class": det_class,
                "detector_confidence": det_conf,
                "annotated_rel": ann_rel,
                "species_short": species_short,
                "species_latin": species_latin,
                "species_type": _species_type_tag(species),
                "species_candidates_json": json.dumps(
                    [{"label": l, "confidence": c} for l, c in species_candidates],
                    ensure_ascii=False,
                ),
                "detector_objects_json": json.dumps(
                    [{"label": l, "confidence": c} for l, c in detector_objects],
                    ensure_ascii=False,
                ),
                **_extract_trailcam_overlay_fields(Path(ann)),
            }
        )

    def _rows_from_output_dir(p: Path) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        if not p.is_dir():
            return rows
        try:
            repo_root = Path(__file__).resolve().parents[1]
            default_input = repo_root / "test-media" / "input"
            runtime_input = Path(
                str(db.get_control("runtime_input_dir", str(default_input)) or str(default_input))
            ).expanduser().resolve(strict=False)
        except Exception:
            runtime_input = None
            default_input = Path(__file__).resolve().parents[1] / "test-media" / "input"
        for ann in sorted(p.glob("*.annotated.jpg"), key=lambda x: x.name.lower()):
            base = re.sub(r"\.annotated\.jpg$", "", ann.name, flags=re.IGNORECASE)
            ml = p / f"{base}.ml.json"
            sp = p / f"{base}.species.json"
            if not ml.is_file() or not sp.is_file():
                continue
            input_candidate = p / f"{base}.jpg"
            if runtime_input is not None:
                runtime_candidate = runtime_input / f"{base}.jpg"
                if runtime_candidate.is_file():
                    input_candidate = runtime_candidate
            if not input_candidate.is_file():
                fallback_candidate = default_input / f"{base}.jpg"
                if fallback_candidate.is_file():
                    input_candidate = fallback_candidate
            rows.append(
                {
                    "input": str(input_candidate),
                    "ml_json": str(ml),
                    "species_json": str(sp),
                    "annotated": str(ann),
                }
            )
        return rows

    for j in jobs:
        source = Path(str(j.get("input_path") or j.get("filename") or "")).name
        raw_outputs = j.get("outputs_json")
        outputs: list[dict[str, object]] = []
        if raw_outputs:
            try:
                parsed = json.loads(str(raw_outputs))
                if isinstance(parsed, list):
                    outputs = [x for x in parsed if isinstance(x, dict)]
            except Exception:
                outputs = []
        # Support partial frame visibility for in-progress runs.
        if not outputs:
            out_dir = Path(str(j.get("output_dir") or ""))
            outputs = _rows_from_output_dir(out_dir)
        for row in outputs:
            _append_record(source, row, j.get("id"))
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


def _home_query(page: int, hide_blanks: bool, summary_page: int, species_mode: str) -> str:
    """Query string for home page links (preserve frame + summary pagination)."""
    hb = 1 if hide_blanks else 0
    mode = species_mode if species_mode in ("short", "latin", "full") else "short"
    return f"page={page}&hide_blanks={hb}&summary_page={summary_page}&species_mode={mode}"


def _record_is_blank(species: str, description: str) -> bool:
    return record_is_blank(species, description)


def _format_species_display(species: str, description: str) -> str:
    return format_species_display(species, description)


def _export_frames_xlsx(records: list[dict[str, str]], hide_blanks: bool) -> bytes:
    return export_frames_xlsx(records, hide_blanks, str(LOG_FILE))


def _render_page(
    msg: str = "",
    page: int = 1,
    hide_blanks: bool = True,
    summary_page: int = 1,
    species_mode: str = "short",
) -> str:
    """Prepare home-page view data and delegate HTML rendering."""
    if species_mode not in ("short", "latin", "full"):
        species_mode = "short"
    input_dir, video_dir, output_dir = _runtime_dirs()
    jobs = db.list_jobs(limit=JOBS_PANEL_LIMIT)
    tags_map = db.get_frame_tags_map()
    try:
        detector_min_confidence = max(
            0.0,
            min(1.0, float(str(db.get_control("detector_min_confidence", "0.0") or "0.0"))),
        )
    except Exception:
        detector_min_confidence = 0.0
    suppress_blank_species_boxes = str(db.get_control("suppress_blank_species_boxes", "1") or "1").strip() == "1"
    # Full list for Video Frame Browser (client-side hide/show blanks).
    all_frame_records = _frame_records(jobs)
    for r in all_frame_records:
        r["manual_tag"] = tags_map.get(r.get("annotated_rel", ""), "")
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
        if j["status"] in ("running", "queued"):
            actions += (
                f"<a class='btn btn-subtle btn-compact js-action' href='/pause-job/{j['id']}' "
                "data-confirm='Pause this run now?\\n\\nYou can continue it later from this Runs card.'>Pause</a> "
            )
        if j["status"] == "done":
            actions += (
                f"<a class='btn btn-subtle btn-compact js-action' href='/retry/{j['id']}' "
                "data-confirm='Reprocess this completed run from the beginning?\\n\\nA fresh output set will be generated.'>Reprocess</a> "
            )
        if j["status"] in ("error", "cancelled"):
            actions += (
                f"<a class='btn btn-subtle btn-compact js-action' href='/continue-job/{j['id']}' "
                "data-confirm='Continue this run?\\n\\nThis resumes from saved progress in the existing run folder.'>Continue</a> "
            )
        if j["status"] == "queued":
            actions += (
                f"<a class='btn btn-subtle btn-compact js-action' href='/cancel/{j['id']}' "
                "data-confirm='Cancel this queued job?'>Cancel</a>"
            )
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
                Path(out_dir).relative_to(ROOT)
                out_link = f"<a class='btn btn-subtle btn-compact' href='/browse-output/{j['id']}'>Output Browser</a>"
                open_link = f"<a class='btn btn-subtle btn-compact' href='/open-output/{j['id']}'>Open Folder</a>"
            except Exception:
                out_link = ""
                open_link = ""
        prog = ""
        total = int(j.get("total_items") or 0)
        done_n = int(j.get("processed_items") or 0)
        if total > 0:
            pct = int((done_n / total) * 100)
            prog = f"<div class='progress'><div class='bar' style='width:{pct}%'></div></div><div class='job-meta'>Progress: {done_n}/{total}</div>"
        err_style = "" if err else "display:none"
        err_html = f'<div id="jobErr_{j["id"]}" class="job-err" style="{err_style}">{html.escape(err)}</div>'
        job_items.append(
            f"<div class='job-card' data-job-id='{j['id']}'>"
            f"<div class='job-head'><div><b>#{j['id']}</b> {j['filename']}</div>"
            f"<span id='jobStatus_{j['id']}' class='status {status_class}'>{j['status']}</span></div>"
            f"<div id='jobMeta_{j['id']}' class='job-meta'>Created: {j.get('created_at','')} | Started: {j.get('started_at') or '-'} | Finished: {j.get('finished_at') or '-'}</div>"
            f"<div id='jobCfg_{j['id']}' class='job-meta'>Detection: conf ≥ {detector_min_confidence:.2f} | suppress blank boxes: {'on' if suppress_blank_species_boxes else 'off'}</div>"
            f"<div id='jobProg_{j['id']}'>{prog}</div>"
            f"<div id='jobLog_{j['id']}' class='job-log'>{last_log or '-'}</div>"
            f"{err_html}"
            f"<div id='jobPreview_{j['id']}'>{preview}</div>"
            f"<div id='jobActions_{j['id']}' class='job-actions'>{actions} {out_link} {open_link}</div>"
            f"</div>"
        )
    summary_pagination_bits: list[str] = []
    if total_summary_sources == 0:
        summary_pagination_bits.append("<span class='job-meta'>0 sources</span>")
    else:
        if summary_page > 1:
            summary_pagination_bits.append(
                f"<a class='btn btn-subtle btn-compact' href='/?{_home_query(page, hide_blanks, summary_page - 1, species_mode)}'>Prev</a>"
            )
        summary_pagination_bits.append(
            f"<span class='job-meta'>Sources {ss + 1}–{min(se, total_summary_sources)} of "
            f"{total_summary_sources} · Page {summary_page} / {summary_total_pages}</span>"
        )
        if summary_page < summary_total_pages:
            summary_pagination_bits.append(
                f"<a class='btn btn-subtle btn-compact' href='/?{_home_query(page, hide_blanks, summary_page + 1, species_mode)}'>Next</a>"
            )
    summary_rows = []
    for v in summary_slice:
        total = int(v["total_frames"])
        proc = int(v["processed_frames"])
        pct = int((proc / total) * 100) if total > 0 else 0
        overall = (
            "running"
            if int(v["running"]) > 0
            else "done"
            if int(v["done"]) > 0
            else "queued"
            if int(v["queued"]) > 0
            else "error"
            if int(v["error"]) > 0
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
        species_short = str(r.get("species_short") or _species_short_name(r["species"]))
        species_latin = str(r.get("species_latin") or _species_latin_name(r["species"]))
        species_full = str(r.get("species") or "")
        if _record_is_blank(r["species"], r["description"]):
            species_disp = "No species match (blank)"
        elif species_mode == "latin":
            species_disp = species_latin or species_short or species_full or "—"
        elif species_mode == "full":
            species_disp = species_full or species_short or "—"
        else:
            species_disp = species_short or species_full or "—"
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
            + " "
            + (r.get("manual_tag") or "")
            + " "
            + (r.get("species_short") or "")
            + " "
            + (r.get("species_latin") or "")
            + " "
            + (r.get("species_type") or "")
            + " "
            + (r.get("species_candidates_json") or "")
            + " "
            + (r.get("detector_objects_json") or "")
            + " blank no species match"
        )
        manual_tag = str(r.get("manual_tag") or "")
        manual_bits = [t.strip() for t in manual_tag.split(",") if t.strip()]
        default_bits = []
        if species_short:
            default_bits.append(species_short)
        if r.get("species_type"):
            default_bits.append(str(r.get("species_type")))
        all_filter_bits = []
        seen_bits: set[str] = set()
        for t in default_bits + manual_bits:
            k = t.lower()
            if k in seen_bits:
                continue
            seen_bits.add(k)
            all_filter_bits.append(t)
        tags_norm = ",".join([t.lower() for t in all_filter_bits])
        default_html = ""
        if default_bits:
            default_chips = "".join([f"<span class='tag-chip default'>{html.escape(t)}</span>" for t in default_bits])
            default_html = f"<div><b>Default tags:</b></div><div class='tag-list'>{default_chips}</div>"
        manual_html = ""
        if manual_bits:
            manual_chips = "".join([f"<span class='tag-chip'>{html.escape(t)}</span>" for t in manual_bits])
            manual_html = f"<div><b>Manual tags:</b></div><div class='tag-list'>{manual_chips}</div>"
        species_candidates_html = ""
        detector_objects_html = ""
        try:
            species_candidates_parsed = json.loads(str(r.get("species_candidates_json") or "[]"))
        except Exception:
            species_candidates_parsed = []
        if isinstance(species_candidates_parsed, list) and species_candidates_parsed:
            chips = []
            for c in species_candidates_parsed[:5]:
                if not isinstance(c, dict):
                    continue
                lbl = str(c.get("label") or "").strip()
                if not lbl:
                    continue
                conf = str(c.get("confidence") or "").strip()
                txt = f"{lbl} ({conf})" if conf else lbl
                chips.append(f"<span class='tag-chip'>{html.escape(txt)}</span>")
            if chips:
                species_candidates_html = f"<div><b>Species candidates:</b></div><div class='tag-list'>{''.join(chips)}</div>"
        try:
            detector_objects_parsed = json.loads(str(r.get("detector_objects_json") or "[]"))
        except Exception:
            detector_objects_parsed = []
        if isinstance(detector_objects_parsed, list) and detector_objects_parsed:
            chips = []
            for c in detector_objects_parsed[:8]:
                if not isinstance(c, dict):
                    continue
                lbl = str(c.get("label") or "").strip()
                if not lbl:
                    continue
                conf = str(c.get("confidence") or "").strip()
                txt = f"{lbl} ({conf})" if conf else lbl
                chips.append(f"<span class='tag-chip'>{html.escape(txt)}</span>")
            if chips:
                detector_objects_html = f"<div><b>Detected objects:</b></div><div class='tag-list'>{''.join(chips)}</div>"
        latin_html = f"<div><b>Latin:</b> {html.escape(species_latin)}</div>" if species_latin and not is_blank else ""
        taxonomy_html = (
            f"<div><b>Taxonomy:</b> {html.escape(species_full)}</div>"
            if species_full and not is_blank and species_full != species_disp
            else ""
        )
        rel_js = json.dumps(str(r.get("annotated_rel") or ""))
        inp_js = json.dumps(str(r.get("input_abs") or ""))
        job_js = json.dumps(str(r.get("job_id") or ""))
        result_rows.append(
            "<div class='result-card result-row' "
            f"data-is-blank='{'1' if is_blank else '0'}' "
            f"data-tags='{html.escape(tags_norm, quote=True)}' "
            f"data-search='{html.escape(search_blob.lower(), quote=True)}'>"
            f"<div><a href='/files/{r['annotated_rel']}' target='_blank'>"
            f"<img src='/files/{r['annotated_rel']}' class='thumb' "
            "onerror=\"this.onerror=null;this.replaceWith(document.createTextNode('Image removed'))\"/></a></div>"
            "<div class='result-text'>"
            f"<div><b>Job:</b> #{r['job_id']}</div>"
            f"<div><b>Video:</b> {html.escape(r['source'])}</div>"
            f"<div><b>Frame:</b> {html.escape(r['frame'])}</div>"
            f"<div><b>Species:</b> {html.escape(species_disp)}</div>"
            f"{latin_html}"
            f"{taxonomy_html}"
            f"{species_candidates_html}{detector_objects_html}{default_html}{manual_html}"
            f"<div class='desc-col' title='{html.escape(r['description'], quote=True)}'>{html.escape(r['description'])}</div>"
            f"<div style='margin-top:4px' class='actions'>"
            f"<button class='btn btn-subtle' type='button' onclick='editManualTag({rel_js})'>Edit tag</button>"
            f"<button class='btn btn-subtle' type='button' onclick='rerunFrame({inp_js}, {job_js})'>Re-run frame</button>"
            "</div>"
            "</div>"
            "</div>"
        )
    pagination_bits: list[str] = []
    if page > 1:
        pagination_bits.append(
            f"<a class='btn btn-subtle btn-compact' href='/?{_home_query(page - 1, hide_blanks, summary_page, species_mode)}'>Prev</a>"
        )
    pagination_bits.append(
        f"<span class='job-meta'>Page {page} / {total_pages} ({total_records} total, "
        f"{FRAME_RESULTS_PAGE_SIZE} per page)</span>"
    )
    if page < total_pages:
        pagination_bits.append(
            f"<a class='btn btn-subtle btn-compact' href='/?{_home_query(page + 1, hide_blanks, summary_page, species_mode)}'>Next</a>"
        )
    has_active = counts.get("queued", 0) > 0 or counts.get("running", 0) > 0
    # Always embed all frames so the video browser checkbox can reveal blanks without a full reload.
    records_json = json.dumps(all_frame_records).replace("</", "<\\/")
    default_input_dir, default_video_dir, default_output_dir = defaults(ROOT)
    output_label = html.escape(output_dir.as_posix())
    input_label = html.escape(input_dir.as_posix())
    video_label = html.escape(video_dir.as_posix())
    return render_home_page_html(
        paused=paused,
        msg=msg,
        counts=counts,
        video_dir_posix=video_dir.as_posix(),
        summary_table_page_size=SUMMARY_TABLE_PAGE_SIZE,
        summary_pagination_bits=summary_pagination_bits,
        summary_rows=summary_rows,
        frame_results_page_size=FRAME_RESULTS_PAGE_SIZE,
        pagination_bits=pagination_bits,
        result_rows=result_rows,
        job_items=job_items,
        output_label=output_label,
        default_input_label=default_input_dir.as_posix(),
        default_video_label=default_video_dir.as_posix(),
        default_output_label=default_output_dir.as_posix(),
        input_label=input_label,
        video_label=video_label,
        hide_blanks=hide_blanks,
        species_mode=species_mode,
        has_active=has_active,
        records_json=records_json,
        detector_min_confidence=detector_min_confidence,
        suppress_blank_species_boxes=suppress_blank_species_boxes,
    )


def _validate_runtime_dir(raw: str, label: str) -> Path:
    return validate_runtime_dir(ROOT, raw, label)


def _worker_loop() -> None:
    """Run the background worker loop until shutdown."""
    run_worker_loop(
        is_stopped=lambda: _stop_worker,
        db=db,
        logger=logger,
        get_runtime_dirs=_runtime_dirs,
        extract_frames=extract_frames,
        process_images=process_images,
    )


register_api_routes(
    app,
    db=db,
    logger=logger,
    parse_exts=_parse_exts,
    folder_media_index=_folder_media_index,
    validate_runtime_dir=_validate_runtime_dir,
    normalize_tags_csv=_normalize_tags_csv,
    frame_records=_frame_records,
    record_is_blank=_record_is_blank,
    export_frames_xlsx=_export_frames_xlsx,
)


@app.get("/", response_class=HTMLResponse)
async def index(
    msg: str = "",
    page: int = 1,
    hide_blanks: int = Query(1, ge=0, le=1),
    summary_page: int = Query(1, ge=1),
    species_mode: str = Query("short"),
) -> str:
    return _render_page(
        msg,
        page=page,
        hide_blanks=bool(hide_blanks),
        summary_page=summary_page,
        species_mode=species_mode,
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
        return RedirectResponse(url="/", status_code=303)
    if jid < 0:
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@app.post("/process-multi")
async def process_multi(
    media_files: list[UploadFile] = File(...),
    fps: float = Form(1.0),
    ml_url: str = Form("http://127.0.0.1:8010"),
    species_url: str = Form("http://127.0.0.1:8100"),
) -> JSONResponse:
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
    return JSONResponse({"ok": True, "queued": queued, "skipped": skipped, "bad": bad})


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
    logger.info("action=retry")
    return RedirectResponse(url="/", status_code=303)


@app.get("/cancel/{job_id}")
async def cancel(job_id: int) -> RedirectResponse:
    db.cancel_job(job_id)
    logger.info("action=cancel")
    return RedirectResponse(url="/", status_code=303)


@app.get("/pause-job/{job_id}")
async def pause_job(job_id: int) -> RedirectResponse:
    db.cancel_job(job_id)
    logger.info("action=pause_job")
    return RedirectResponse(url="/", status_code=303)


@app.get("/continue-job/{job_id}")
async def continue_job(job_id: int) -> RedirectResponse:
    job = db.get_job(job_id)
    status = str((job or {}).get("status") or "")
    if status == "running":
        return RedirectResponse(url="/", status_code=303)
    if status == "queued":
        return RedirectResponse(url="/", status_code=303)
    db.resume_job(job_id)
    logger.info("action=continue_job_resume")
    return RedirectResponse(url="/", status_code=303)


@app.get("/cancel-all")
async def cancel_all() -> RedirectResponse:
    n = db.cancel_all_active()
    logger.info("cancel_all affected=%s", n)
    return RedirectResponse(url="/", status_code=303)


@app.get("/clear-jobs")
async def clear_jobs() -> RedirectResponse:
    if db.has_running_jobs():
        return RedirectResponse(url="/", status_code=303)
    n = db.clear_all_jobs()
    logger.info("clear_jobs removed=%s", n)
    return RedirectResponse(url="/", status_code=303)


@app.get("/reset-all")
async def reset_all() -> RedirectResponse:
    cancelled = db.cancel_all_active()
    input_dir, video_dir, output_dir = _runtime_dirs()
    if output_dir.is_dir():
        for d in output_dir.glob("run_*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    if input_dir.is_dir():
        for f in input_dir.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception as exc:
                    logger.debug("reset_all_input_cleanup_failed path=%s err=%s", f, exc)
    if video_dir.is_dir():
        for f in video_dir.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except Exception as exc:
                    logger.debug("reset_all_video_cleanup_failed path=%s err=%s", f, exc)
    _ensure_dir_gitkeep(output_dir)
    _ensure_dir_gitkeep(input_dir)
    _ensure_dir_gitkeep(video_dir)
    removed = db.clear_all_jobs()
    logger.info("reset_all cancelled=%s cleared=%s", cancelled, removed)
    return RedirectResponse(url="/", status_code=303)


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
    except Exception as exc:
        logger.debug("same_origin_referer_parse_failed referer=%s err=%s", _safe_log_value(ref), exc)
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


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_probe() -> Response:
    # Chrome probes this path in some contexts; return no-content to avoid noisy 404 logs.
    return Response(status_code=204)


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
            except Exception as exc:
                logger.debug("browse_output_species_json_failed path=%s err=%s", sp_path, exc)
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
            except Exception as exc:
                logger.debug("browse_output_ml_json_failed path=%s err=%s", ml_path, exc)
        rel = ann.relative_to(ROOT).as_posix()
        sp_short = _species_short_name(species)
        sp_latin = _species_latin_name(species)
        desc = (
            f"Likely {sp_short}" + (f" ({sp_latin})" if sp_latin else "")
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
    page_html = render_output_browser_page(job_id, rel_dir, "".join(rows))
    return HTMLResponse(page_html)


@app.get("/cleanup-output")
async def cleanup_output() -> RedirectResponse:
    active_dirs: set[str] = set()
    for j in db.list_jobs(limit=JOBS_PANEL_LIMIT):
        if j.get("status") in ("queued", "running") and j.get("output_dir"):
            active_dirs.add(str(j.get("output_dir")))
    _, _, output_dir = _runtime_dirs()
    if output_dir.is_dir():
        for d in output_dir.glob("run_*"):
            if d.is_dir() and str(d) not in active_dirs:
                shutil.rmtree(d, ignore_errors=True)
    _ensure_dir_gitkeep(output_dir)
    logger.info("cleanup_output done")
    return RedirectResponse(url="/", status_code=303)


@app.get("/reset-generated-media")
async def reset_generated_media() -> RedirectResponse:
    active_output_dirs: set[str] = set()
    for j in db.list_jobs(limit=JOBS_PANEL_LIMIT):
        if j.get("status") in ("queued", "running") and j.get("output_dir"):
            active_output_dirs.add(str(j.get("output_dir")))
    input_dir, video_dir, output_dir = _runtime_dirs()
    removed_out = 0
    removed_in = 0
    removed_vid = 0
    if output_dir.is_dir():
        for d in output_dir.glob("run_*"):
            if d.is_dir() and str(d) not in active_output_dirs:
                shutil.rmtree(d, ignore_errors=True)
                removed_out += 1
    _ensure_dir_gitkeep(output_dir)
    if input_dir.is_dir():
        for f in input_dir.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    removed_in += 1
                except Exception as exc:
                    logger.debug("reset_generated_media_input_cleanup_failed path=%s err=%s", f, exc)
    if video_dir.is_dir():
        for f in video_dir.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    removed_vid += 1
                except Exception as exc:
                    logger.debug("reset_generated_media_video_cleanup_failed path=%s err=%s", f, exc)
    _ensure_dir_gitkeep(input_dir)
    _ensure_dir_gitkeep(video_dir)
    logger.info(
        "reset_generated_media outputs_removed=%s input_removed=%s video_removed=%s",
        removed_out,
        removed_in,
        removed_vid,
    )
    return RedirectResponse(url="/", status_code=303)
