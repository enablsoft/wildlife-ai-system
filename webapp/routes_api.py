from __future__ import annotations

"""
API route registration for the wildlife webapp.

Function index:
- register_api_routes: register all `/api/*` and `/export/*` endpoints.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field


class EnqueueFolderPreviewIn(BaseModel):
    """Define request payload for folder queue preview."""
    folder_path: str
    exts: str = ".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp"


class EnqueueFolderCommitIn(BaseModel):
    """Define request payload for committing selected folder files."""
    folder_path: str
    exts: str = ".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.webp"
    fps: float = 1.0
    ml_url: str = "http://127.0.0.1:8010"
    species_url: str = "http://127.0.0.1:8100"
    input_paths: list[str] = Field(default_factory=list)


class RuntimeSettingsIn(BaseModel):
    """Define request payload for runtime folder settings."""
    input_dir: str
    video_dir: str
    output_dir: str


class FrameTagIn(BaseModel):
    """Define request payload for manual frame tags."""
    annotated_rel: str
    tag_text: str = ""


def register_api_routes(
    app: Any,
    *,
    db: Any,
    logger: Any,
    parse_exts: Callable[[str], set[str]],
    folder_media_index: Callable[[Path, set[str]], dict[str, tuple[Path, str]]],
    validate_runtime_dir: Callable[[str, str], Path],
    normalize_tags_csv: Callable[[str], str],
    frame_records: Callable[[list[dict[str, object]]], list[dict[str, str]]],
    record_is_blank: Callable[[str, str], bool],
    export_frames_xlsx: Callable[[list[dict[str, str]], bool], bytes],
) -> None:
    """Register API endpoints used by UI actions and exports."""

    def _resolve_batch_folder(raw_folder: str) -> tuple[Path | None, str | None]:
        raw = (raw_folder or "").strip().strip('"').strip("'")
        if not raw:
            return None, "Folder path is required."
        repo_root = Path(__file__).resolve().parents[1]
        default_video_root = (repo_root / "test-media" / "video").resolve(strict=False)
        raw_video_root = db.get_control("runtime_video_dir", str(default_video_root))
        try:
            video_root = Path(raw_video_root).expanduser().resolve(strict=False)
        except Exception:
            video_root = default_video_root

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (video_root / candidate).resolve(strict=False)

        try:
            resolved = candidate.resolve(strict=True)
        except Exception:
            return None, "Folder not found."
        if not resolved.is_dir():
            return None, "Folder path must be a directory."
        if resolved != video_root and video_root not in resolved.parents:
            return None, f"Folder must be inside runtime video folder: {video_root}"
        return resolved, None

    @app.post("/api/settings/runtime")
    async def api_settings_runtime(body: RuntimeSettingsIn) -> JSONResponse:
        try:
            input_dir = validate_runtime_dir(body.input_dir, "Input folder")
            video_dir = validate_runtime_dir(body.video_dir, "Video folder")
            output_dir = validate_runtime_dir(body.output_dir, "Output folder")
        except ValueError as e:
            logger.warning("settings_runtime_validation_failed error=%s", e)
            return JSONResponse({"ok": False, "error": "Invalid runtime folder settings."}, status_code=400)
        db.set_control("runtime_input_dir", str(input_dir))
        db.set_control("runtime_video_dir", str(video_dir))
        db.set_control("runtime_output_dir", str(output_dir))
        return JSONResponse(
            {
                "ok": True,
                "input_dir": str(input_dir),
                "video_dir": str(video_dir),
                "output_dir": str(output_dir),
            }
        )

    @app.post("/api/frame-tag")
    async def api_frame_tag(body: FrameTagIn) -> JSONResponse:
        rel = (body.annotated_rel or "").strip()
        if not rel:
            return JSONResponse({"ok": False, "error": "annotated_rel is required"}, status_code=400)
        tag = normalize_tags_csv((body.tag_text or "").strip())
        if tag:
            db.upsert_frame_tag(rel, tag)
        else:
            db.remove_frame_tag(rel)
        logger.info("frame_tag_updated rel=%s has_tag=%s", rel, bool(tag))
        return JSONResponse({"ok": True, "annotated_rel": rel, "tag_text": tag})

    @app.post("/api/enqueue-folder-preview")
    async def api_enqueue_folder_preview(body: EnqueueFolderPreviewIn) -> JSONResponse:
        p, folder_err = _resolve_batch_folder(body.folder_path)
        if folder_err or p is None:
            return JSONResponse({"ok": False, "error": folder_err or "Invalid folder path."}, status_code=400)
        wanted = parse_exts(body.exts)
        try:
            idx = folder_media_index(p, wanted)
        except Exception:
            logger.exception("enqueue_folder_preview_failed folder=%s", p)
            return JSONResponse({"ok": False, "error": "Unable to enumerate media files for this folder."}, status_code=400)
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
        return JSONResponse({"ok": True, "folder": str(p), "items": items})

    @app.post("/api/enqueue-folder-commit")
    async def api_enqueue_folder_commit(body: EnqueueFolderCommitIn) -> JSONResponse:
        p, folder_err = _resolve_batch_folder(body.folder_path)
        if folder_err or p is None:
            return JSONResponse({"ok": False, "error": folder_err or "Invalid folder path."}, status_code=400)
        wanted = parse_exts(body.exts)
        try:
            idx = folder_media_index(p, wanted)
        except Exception:
            logger.exception("enqueue_folder_commit_failed folder=%s", p)
            return JSONResponse({"ok": False, "error": "Unable to enumerate media files for this folder."}, status_code=400)
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
        logger.info("batch_enqueued_commit count=%s skipped=%s missing=%s folder=%s", q, skipped, missing, p)
        msg = f"Queued {q} file(s), skipped {skipped} active duplicate(s), {missing} path(s) not in folder."
        return JSONResponse({"ok": True, "queued": q, "skipped": skipped, "missing": missing, "message": msg})

    @app.get("/export/frame-results.xlsx")
    async def export_frame_results_xlsx(
        hide_blanks: int = Query(1, ge=0, le=1),
    ) -> Response:
        records = frame_records(db.list_all_jobs())
        hide = bool(hide_blanks)
        if hide:
            records = [r for r in records if not record_is_blank(r["species"], r["description"])]
        try:
            payload = export_frames_xlsx(records, hide)
        except RuntimeError:
            logger.exception("export_frame_results_xlsx_failed")
            return JSONResponse({"ok": False, "error": "Excel export is currently unavailable."}, status_code=500)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fn = f"wildlife_frame_results_{ts}.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{fn}"'}
        logger.info("export_xlsx rows=%s hide_blanks=%s filename=%s", len(records), hide, fn)
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

