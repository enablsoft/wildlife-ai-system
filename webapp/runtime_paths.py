from __future__ import annotations

"""
Runtime path resolution and validation helpers.

Function index:
- defaults: build default `test-media` folder layout.
- to_abs_path: normalize relative or absolute configured paths.
- runtime_dirs: resolve active runtime folders from DB controls.
- validate_runtime_dir: validate and prepare configured runtime folder.
"""

from pathlib import Path

from webapp.jobs_db import JobsDb


def defaults(root: Path) -> tuple[Path, Path, Path]:
    """Build default input, video, and output folders under `test-media`."""
    base = root / "test-media"
    return base / "input", base / "video", base / "output"


def to_abs_path(root: Path, raw: str | None, fallback: Path) -> Path:
    """Resolve a configured path to absolute form with fallback when empty."""
    value = (raw or "").strip()
    if not value:
        return fallback
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (root / p).resolve()
    return p


def runtime_dirs(root: Path, db: JobsDb) -> tuple[Path, Path, Path]:
    """Resolve runtime folders from persisted controls in the database."""
    default_input, default_video, default_output = defaults(root)
    input_dir = to_abs_path(
        root,
        db.get_control("runtime_input_dir", str(default_input)),
        default_input,
    )
    video_dir = to_abs_path(
        root,
        db.get_control("runtime_video_dir", str(default_video)),
        default_video,
    )
    output_dir = to_abs_path(
        root,
        db.get_control("runtime_output_dir", str(default_output)),
        default_output,
    )
    return input_dir, video_dir, output_dir


def validate_runtime_dir(root: Path, raw: str, label: str) -> Path:
    """Validate and create a runtime folder submitted from settings UI."""
    p = to_abs_path(root, raw, root)
    if not str(raw or "").strip():
        raise ValueError(f"{label} is required.")
    p.mkdir(parents=True, exist_ok=True)
    if not p.is_dir():
        raise ValueError(f"{label} is not a directory: {p}")
    return p

