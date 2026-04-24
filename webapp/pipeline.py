from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEOS = {".mp4", ".mov", ".avi", ".mkv"}


def _is_blank_species_label(value: str | None) -> bool:
    s = str(value or "").strip().lower()
    if not s:
        return True
    if "__blank" in s:
        return True
    parts = [p.strip() for p in s.split(";") if p.strip()]
    return bool(parts and parts[-1] == "blank")


def _compact_species_label(value: str | None, max_len: int = 36) -> str:
    if not value:
        return ""
    raw = value.replace("_", " ").strip()
    # Species services may return taxonomy chains like:
    # "id;class;order;family;genus;species;Common Name"
    if ";" in raw:
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        if parts:
            raw = parts[-1]
    cleaned = " ".join(raw.split())
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def extract_frames(video_path: Path, frames_dir: Path, fps: float = 1.0) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem
    out_pattern = frames_dir / f"{stem}_frame_%04d.jpg"
    ffmpeg_bin = _resolve_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        str(out_pattern),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return sorted(p for p in frames_dir.glob(f"{stem}_frame_*.jpg") if p.is_file())


def _resolve_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    winget_pkg = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    if winget_pkg.is_dir():
        # Support multiple ffmpeg versions (e.g. ffmpeg-8.1-full_build, ffmpeg-8.2-...)
        for p in sorted(winget_pkg.glob("ffmpeg-*-full_build/bin/ffmpeg.exe"), reverse=True):
            if p.is_file():
                return str(p)
    raise FileNotFoundError(
        "ffmpeg not found in PATH or Winget cache; install ffmpeg and restart terminal."
    )


def call_detector(image_path: Path, ml_url: str) -> dict[str, Any]:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    r = requests.post(
        f"{ml_url.rstrip('/')}/detect-base64",
        json={"image_base64": b64},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()


def call_species(image_path: Path, species_url: str) -> dict[str, Any]:
    with image_path.open("rb") as f:
        r = requests.post(
            f"{species_url.rstrip('/')}/predict",
            files={"image": (image_path.name, f, "image/jpeg")},
            timeout=240,
        )
    r.raise_for_status()
    return r.json()


def draw_boxes(
    image_path: Path,
    det: dict[str, Any],
    out_path: Path,
    species_label: str | None = None,
    species_score: float | None = None,
    min_detector_confidence: float = 0.0,
    suppress_when_blank_species: bool = False,
) -> None:
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    font_size = max(13, min(20, int(h * 0.022)))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    clean_species = _compact_species_label(species_label if isinstance(species_label, str) else None)
    if suppress_when_blank_species and _is_blank_species_label(species_label):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path)
        return
    drawn_boxes = 0
    for obj in det.get("objects") or []:
        bbox = obj.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        conf_raw = obj.get("confidence")
        conf = float(conf_raw) if isinstance(conf_raw, (float, int)) else 0.0
        if conf < max(0.0, float(min_detector_confidence)):
            continue
        drawn_boxes += 1
        x1, y1, x2, y2 = bbox
        d.rectangle((x1, y1, x2, y2), outline=(144, 238, 144), width=3)
        det_class = str(obj.get("class", "?")).replace("_", " ").strip().lower()
        det_conf = conf * 100.0
        line1 = f"{det_class}: {det_conf:.0f}%"
        line2 = ""
        if clean_species:
            if isinstance(species_score, (float, int)):
                line2 = f"{clean_species.lower()}: {float(species_score) * 100.0:.1f}%"
            else:
                line2 = clean_species.lower()
        label_lines = [line1] + ([line2] if line2 else [])
        label = "\n".join(label_lines)
        tx = int(x1 + 4)
        ty = int(max(0, y1 - (font_size * max(1, len(label_lines))) - 12))
        try:
            l, t, r, b = d.multiline_textbbox((0, 0), label, font=font, spacing=2)
            tw, th = (r - l), (b - t)
        except Exception:
            longest = max(len(x) for x in label_lines)
            tw, th = (longest * max(8, font_size // 2), (font_size + 4) * len(label_lines))
        d.rectangle((tx - 3, ty - 2, tx + tw + 4, ty + th + 2), fill=(15, 23, 42))
        d.multiline_text((tx, ty), label, fill=(167, 243, 208), font=font, spacing=2)
    if drawn_boxes == 0 and clean_species:
        # Fallback: when detector returns no drawable boxes, still annotate the frame
        # so users can see species output is present on this image.
        x1, y1, x2, y2 = 8, 8, max(9, w - 8), max(9, h - 8)
        d.rectangle((x1, y1, x2, y2), outline=(250, 220, 235), width=1)
        if isinstance(species_score, (float, int)):
            label = f"{clean_species.lower()}: {float(species_score) * 100.0:.1f}% (no detector box)"
        else:
            label = f"{clean_species.lower()} (no detector box)"
        tx = x1 + 6
        ty = y1 + 6
        try:
            l, t, r, b = d.textbbox((0, 0), label, font=font)
            tw, th = (r - l), (b - t)
        except Exception:
            tw, th = (len(label) * max(8, font_size // 2), font_size + 4)
        d.rectangle((tx - 3, ty - 2, tx + tw + 4, ty + th + 2), fill=(30, 41, 59))
        d.text((tx, ty), label, fill=(226, 232, 240), font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)


def process_images(
    images: list[Path],
    output_dir: Path,
    ml_url: str,
    species_url: str,
    progress_cb: Any | None = None,
    should_continue_cb: Any | None = None,
    min_detector_confidence: float = 0.0,
    suppress_blank_species_boxes: bool = False,
) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for idx, img in enumerate(images, start=1):
        if should_continue_cb is not None and not bool(should_continue_cb()):
            raise RuntimeError("Job cancelled by user.")
        name = img.stem
        ml_json = output_dir / f"{name}.ml.json"
        sp_json = output_dir / f"{name}.species.json"
        ann_img = output_dir / f"{name}.annotated.jpg"
        det = call_detector(img, ml_url)
        sp = call_species(img, species_url)
        ml_json.write_text(json.dumps(det, indent=2), encoding="utf-8")
        sp_json.write_text(json.dumps(sp, indent=2), encoding="utf-8")
        species_label = sp.get("prediction")
        species_score = sp.get("score")
        draw_boxes(
            img,
            det,
            ann_img,
            species_label=species_label if isinstance(species_label, str) else None,
            species_score=float(species_score) if isinstance(species_score, (int, float)) else None,
            min_detector_confidence=min_detector_confidence,
            suppress_when_blank_species=suppress_blank_species_boxes,
        )
        rows.append(
            {
                "input": str(img),
                "ml_json": str(ml_json),
                "species_json": str(sp_json),
                "annotated": str(ann_img),
            }
        )
        if progress_cb is not None:
            progress_cb(idx, len(images), img)
    return rows
