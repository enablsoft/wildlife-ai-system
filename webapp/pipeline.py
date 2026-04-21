from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEOS = {".mp4", ".mov", ".avi", ".mkv"}


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


def draw_boxes(image_path: Path, det: dict[str, Any], out_path: Path, species_label: str | None = None) -> None:
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    for obj in det.get("objects") or []:
        bbox = obj.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        d.rectangle((x1, y1, x2, y2), outline=(144, 238, 144), width=3)
        base = f"{obj.get('class', '?')} {float(obj.get('confidence', 0.0)):.2f}"
        label = base if not species_label else f"{base} | {species_label}"
        d.text((x1 + 2, max(y1 - 12, 0)), label, fill=(144, 238, 144))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)


def process_images(
    images: list[Path],
    output_dir: Path,
    ml_url: str,
    species_url: str,
) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for img in images:
        name = img.stem
        ml_json = output_dir / f"{name}.ml.json"
        sp_json = output_dir / f"{name}.species.json"
        ann_img = output_dir / f"{name}.annotated.jpg"
        det = call_detector(img, ml_url)
        sp = call_species(img, species_url)
        ml_json.write_text(json.dumps(det, indent=2), encoding="utf-8")
        sp_json.write_text(json.dumps(sp, indent=2), encoding="utf-8")
        species_label = sp.get("prediction")
        draw_boxes(img, det, ann_img, species_label=species_label if isinstance(species_label, str) else None)
        rows.append(
            {
                "input": str(img),
                "ml_json": str(ml_json),
                "species_json": str(sp_json),
                "annotated": str(ann_img),
            }
        )
    return rows
