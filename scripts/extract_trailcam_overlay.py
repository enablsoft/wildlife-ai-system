#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps


def _preprocess_footer(image: Image.Image, footer_ratio: float) -> Image.Image:
    w, h = image.size
    y0 = max(0, int(h * (1.0 - footer_ratio)))
    footer = image.crop((0, y0, w, h)).convert("L")
    footer = ImageEnhance.Contrast(footer).enhance(2.8)
    footer = ImageEnhance.Sharpness(footer).enhance(2.0)
    footer = ImageOps.autocontrast(footer)
    # Binary threshold for camera stamp text.
    return footer.point(lambda p: 255 if p > 128 else 0)


def _ocr_with_tesseract(img_path: Path) -> str:
    tesseract_bin = shutil.which("tesseract")
    if not tesseract_bin:
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        ]
        for c in candidates:
            if Path(c).is_file():
                tesseract_bin = c
                break
    if not tesseract_bin:
        raise RuntimeError("tesseract executable not found in PATH")
    cp = subprocess.run(
        [tesseract_bin, str(img_path), "stdout", "--psm", "6"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "tesseract failed")
    return cp.stdout


def _extract_fields(text: str) -> dict[str, str | None]:
    temperature = None
    date = None
    time = None

    temp_match = re.search(r"\b(-?\d{1,2})\s*([CF])\b", text, flags=re.IGNORECASE)
    if temp_match:
        temperature = f"{temp_match.group(1)}{temp_match.group(2).upper()}"

    date_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
    if date_match:
        date = date_match.group(1)

    time_match = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", text, flags=re.IGNORECASE)
    if time_match:
        time = re.sub(r"\s+", "", time_match.group(1).upper())

    return {"temperature": temperature, "date": date, "time": time}


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR trail-cam footer overlay and parse temperature/date/time.")
    parser.add_argument("image", help="Input image path")
    parser.add_argument("--footer-ratio", type=float, default=0.17, help="Bottom image ratio to OCR (default: 0.17)")
    parser.add_argument("--save-preprocessed", default="", help="Optional output path to save preprocessed footer image")
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        print(f"ERROR: image not found: {image_path}")
        return 2

    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as exc:
        print(f"ERROR: could not open image: {exc}")
        return 2

    processed = _preprocess_footer(image, max(0.05, min(0.4, args.footer_ratio)))

    temp_path = None
    if args.save_preprocessed:
        temp_path = Path(args.save_preprocessed).expanduser().resolve()
    else:
        temp_path = image_path.with_suffix(".footer_ocr.png")

    processed.save(temp_path)

    try:
        ocr_text = _ocr_with_tesseract(temp_path)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("Install Tesseract OCR and ensure 'tesseract' is in PATH.")
        return 3

    fields = _extract_fields(ocr_text)
    result = {
        "image": str(image_path),
        "preprocessed_footer": str(temp_path),
        "ocr_text": ocr_text.strip(),
        "parsed": fields,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
