from __future__ import annotations

"""
API route registration for the wildlife webapp.

Function index:
- register_api_routes: register all `/api/*` and `/export/*` endpoints.
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from webapp.pipeline import process_images


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
    output_dir_override: str = ""
    input_paths: list[str] = Field(default_factory=list)


class RuntimeSettingsIn(BaseModel):
    """Define request payload for runtime folder settings."""
    input_dir: str
    video_dir: str
    output_dir: str


class DetectionSettingsIn(BaseModel):
    """Define request payload for detection filtering settings."""
    detector_min_confidence: float = 0.0
    suppress_blank_species_boxes: bool = True


class FrameTagIn(BaseModel):
    """Define request payload for manual frame tags."""
    annotated_rel: str
    tag_text: str = ""


class RerunFrameIn(BaseModel):
    """Define request payload for re-running a specific frame/image."""
    input_path: str
    job_id: int | None = None


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
        try:
            video_root_real = video_root.resolve(strict=True)
        except Exception:
            return None, "Runtime video folder is unavailable."
        expanded = os.path.expanduser(raw)
        normalized_input = Path(os.path.normpath(expanded))
        if normalized_input.is_absolute():
            candidate = normalized_input.resolve(strict=False)
        else:
            candidate = (video_root_real / normalized_input).resolve(strict=False)
        candidate_norm = os.path.normcase(str(candidate))
        video_root_norm = os.path.normcase(str(video_root_real))
        try:
            within_video_root = os.path.commonpath([candidate_norm, video_root_norm]) == video_root_norm
        except ValueError:
            within_video_root = False
        if not within_video_root:
            return None, f"Folder must be inside runtime video folder: {video_root_real}"

        try:
            resolved = candidate.resolve(strict=True)
        except Exception:
            return None, "Folder not found."
        try:
            resolved.relative_to(video_root_real)
        except Exception:
            return None, f"Folder must be inside runtime video folder: {video_root_real}"
        if not resolved.is_dir():
            return None, "Folder path must be a directory."
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

    @app.post("/api/settings/detection")
    async def api_settings_detection(body: DetectionSettingsIn) -> JSONResponse:
        conf = max(0.0, min(1.0, float(body.detector_min_confidence)))
        suppress_blank = bool(body.suppress_blank_species_boxes)
        db.set_control("detector_min_confidence", f"{conf:.3f}")
        db.set_control("suppress_blank_species_boxes", "1" if suppress_blank else "0")
        return JSONResponse(
            {
                "ok": True,
                "detector_min_confidence": conf,
                "suppress_blank_species_boxes": suppress_blank,
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
        output_override_raw = str(body.output_dir_override or "").strip()
        output_override: Path | None = None
        if output_override_raw:
            try:
                output_override = validate_runtime_dir(output_override_raw, "Output folder")
            except ValueError as e:
                logger.warning("enqueue_folder_commit_invalid_output_override error=%s", e)
                return JSONResponse({"ok": False, "error": "Invalid output folder override."}, status_code=400)
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
                if output_override is not None:
                    run_dir = output_override / f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_job{jid}"
                    db.set_output_dir(jid, str(run_dir))
                q += 1
        logger.info("batch_enqueued_commit count=%s skipped=%s missing=%s folder=%s", q, skipped, missing, p)
        msg = f"Queued {q} file(s), skipped {skipped} active duplicate(s), {missing} path(s) not in folder."
        return JSONResponse({"ok": True, "queued": q, "skipped": skipped, "missing": missing, "message": msg})

    @app.get("/api/jobs-live")
    async def api_jobs_live(limit: int = Query(200, ge=1, le=1000)) -> JSONResponse:
        jobs = db.list_jobs(limit=int(limit))
        repo_root = Path(__file__).resolve().parents[1]
        payload: list[dict[str, Any]] = []
        try:
            live_conf = max(0.0, min(1.0, float(str(db.get_control("detector_min_confidence", "0.0") or "0.0"))))
        except Exception:
            live_conf = 0.0
        suppress_blank = str(db.get_control("suppress_blank_species_boxes", "1") or "1").strip() == "1"
        for j in jobs:
            logs = str(j.get("logs") or "").strip().splitlines()
            source = Path(str(j.get("input_path") or j.get("filename") or "")).name
            status = str(j.get("status") or "")
            preview_rel = ""
            raw_outputs = j.get("outputs_json")
            if raw_outputs:
                try:
                    outputs = json.loads(str(raw_outputs))
                    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
                        ann = Path(str(outputs[0].get("annotated") or ""))
                        if ann.is_file():
                            try:
                                preview_rel = ann.relative_to(repo_root).as_posix()
                            except Exception:
                                preview_rel = ""
                except Exception:
                    preview_rel = ""
            out_dir = str(j.get("output_dir") or "")
            has_out_links = False
            if out_dir:
                try:
                    Path(out_dir).relative_to(repo_root)
                    has_out_links = True
                except Exception:
                    has_out_links = False
            payload.append(
                {
                    "id": int(j.get("id") or 0),
                    "status": status,
                    "source": source,
                    "total_items": int(j.get("total_items") or 0),
                    "processed_items": int(j.get("processed_items") or 0),
                    "created_at": str(j.get("created_at") or ""),
                    "started_at": str(j.get("started_at") or ""),
                    "finished_at": str(j.get("finished_at") or ""),
                    "last_log": logs[-1] if logs else "",
                    "error_text": str(j.get("error_text") or ""),
                    "preview_rel": preview_rel,
                    "can_pause": status in ("running", "queued"),
                    "can_reprocess": status == "done",
                    "can_continue": status in ("error", "cancelled"),
                    "can_cancel": status == "queued",
                    "has_out_links": has_out_links,
                    "detector_min_confidence": live_conf,
                    "suppress_blank_species_boxes": suppress_blank,
                }
            )
        return JSONResponse({"ok": True, "jobs": payload})

    @app.get("/api/frame-records-live")
    async def api_frame_records_live(
        hide_blanks: int = Query(1, ge=0, le=1),
        limit: int = Query(500, ge=1, le=2000),
    ) -> JSONResponse:
        jobs = db.list_jobs(limit=int(limit))
        records = frame_records(jobs)
        tags_map = db.get_frame_tags_map()
        for r in records:
            r["manual_tag"] = tags_map.get(r.get("annotated_rel", ""), "")
        if bool(hide_blanks):
            records = [r for r in records if not record_is_blank(r["species"], r["description"])]
        has_active = any(str(j.get("status") or "") in ("queued", "running") for j in jobs)
        return JSONResponse({"ok": True, "records": records, "has_active": has_active})

    @app.post("/api/rerun-frame")
    async def api_rerun_frame(body: RerunFrameIn) -> JSONResponse:
        raw_path = (body.input_path or "").strip()
        if not raw_path:
            return JSONResponse({"ok": False, "error": "input_path is required"}, status_code=400)
        if not body.job_id:
            return JSONResponse({"ok": False, "error": "job_id is required for in-place frame rerun."}, status_code=400)
        j = db.get_job(int(body.job_id))
        if not j:
            return JSONResponse({"ok": False, "error": "Job not found."}, status_code=404)

        # Only allow rerun for frames already associated with this job.
        allowed_inputs: set[Path] = set()
        raw_outputs = j.get("outputs_json")
        if raw_outputs:
            try:
                outputs = json.loads(str(raw_outputs))
            except Exception:
                outputs = []
            if isinstance(outputs, list):
                for row in outputs:
                    if not isinstance(row, dict):
                        continue
                    p_in = str(row.get("input") or "").strip()
                    if not p_in:
                        continue
                    try:
                        allowed_inputs.add(Path(p_in).resolve(strict=False))
                    except Exception:
                        continue
        if not allowed_inputs:
            # Single-image jobs may not have outputs_json populated yet in some test/migration states.
            raw_job_input = str(j.get("input_path") or "").strip()
            if raw_job_input:
                try:
                    job_input_resolved = Path(raw_job_input).resolve(strict=False)
                except Exception:
                    job_input_resolved = None
                if job_input_resolved is not None:
                    allowed_inputs.add(job_input_resolved)
        if not allowed_inputs:
            return JSONResponse(
                {"ok": False, "error": "No recorded frames found for this job yet."},
                status_code=409,
            )

        requested_norm = os.path.normcase(os.path.normpath(os.path.expanduser(raw_path)))
        allowed_by_norm = {os.path.normcase(os.path.normpath(str(candidate))): candidate for candidate in allowed_inputs}
        matched_input = allowed_by_norm.get(requested_norm)
        if matched_input is None:
            return JSONResponse({"ok": False, "error": "Frame is not part of this job."}, status_code=403)
        try:
            resolved = matched_input.resolve(strict=True)
        except Exception:
            return JSONResponse({"ok": False, "error": "Input frame not found."}, status_code=400)
        if not resolved.is_file():
            return JSONResponse({"ok": False, "error": "Input path must be a file."}, status_code=400)
        if resolved.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return JSONResponse({"ok": False, "error": "Only image frames can be re-run."}, status_code=400)
        out_dir_raw = str(j.get("output_dir") or "").strip()
        if out_dir_raw:
            out_dir = Path(out_dir_raw)
        else:
            repo_root = Path(__file__).resolve().parents[1]
            default_output = Path(db.get_control("runtime_output_dir", str(repo_root / "test-media" / "output")))
            out_dir = default_output / f"run_manual_job{int(body.job_id)}"
            db.set_output_dir(int(body.job_id), str(out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)

        ml_url = str(j.get("ml_url") or "http://127.0.0.1:8010")
        species_url = str(j.get("species_url") or "http://127.0.0.1:8100")
        rows = process_images(
            [resolved],
            out_dir,
            ml_url=ml_url,
            species_url=species_url,
            min_detector_confidence=max(
                0.0,
                min(1.0, float(str(db.get_control("detector_min_confidence", "0.0") or "0.0"))),
            ),
            suppress_blank_species_boxes=str(db.get_control("suppress_blank_species_boxes", "1") or "1").strip().lower()
            in ("1", "true", "yes", "on"),
        )
        if rows:
            db.upsert_output_row(int(body.job_id), rows[0])
            db.append_log(int(body.job_id), f"Frame re-run: {resolved.name}")
        logger.info("frame_rerun_in_place job=%s input=%s", body.job_id, resolved)
        return JSONResponse({"ok": True, "job_id": int(body.job_id)})

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
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fn = f"wildlife_frame_results_{ts}.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{fn}"'}
        logger.info("export_xlsx rows=%s hide_blanks=%s filename=%s", len(records), hide, fn)
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

