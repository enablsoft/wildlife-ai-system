from __future__ import annotations

"""
Background worker execution loop for queued jobs.

Function index:
- run_worker_loop: poll queue, run pipeline, and persist results.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def _existing_row_for_image(output_dir: Path, image: Path) -> dict[str, str] | None:
    name = image.stem
    ml_json = output_dir / f"{name}.ml.json"
    sp_json = output_dir / f"{name}.species.json"
    ann_img = output_dir / f"{name}.annotated.jpg"
    if ml_json.is_file() and sp_json.is_file() and ann_img.is_file():
        return {
            "input": str(image),
            "ml_json": str(ml_json),
            "species_json": str(sp_json),
            "annotated": str(ann_img),
        }
    return None


def _split_completed_images(images: list[Path], output_dir: Path) -> tuple[list[dict[str, str]], list[Path]]:
    done_rows: list[dict[str, str]] = []
    pending: list[Path] = []
    for img in images:
        row = _existing_row_for_image(output_dir, img)
        if row:
            done_rows.append(row)
        else:
            pending.append(img)
    return done_rows, pending


def run_worker_loop(
    *,
    is_stopped: Callable[[], bool],
    db: Any,
    logger: Any,
    get_runtime_dirs: Callable[[], tuple[Path, Path, Path]],
    extract_frames: Callable[..., list[Path]],
    process_images: Callable[..., list[dict[str, str]]],
) -> None:
    """Process queued jobs continuously until a stop signal is set."""
    logger.info("Worker loop started")
    while not is_stopped():
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
            input_dir, _, output_dir = get_runtime_dirs()
            input_path = Path(job["input_path"])
            prior_out = str(job.get("output_dir") or "").strip()
            if prior_out:
                out_dir = Path(prior_out)
            else:
                out_dir = output_dir / f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_job{jid}"
            out_dir.mkdir(parents=True, exist_ok=True)
            db.set_output_dir(jid, str(out_dir))
            if job["media_type"] == "video":
                db.append_log(jid, "Extracting frames")
                logger.info("job_id=%s stage=extract_frames input=%s", jid, input_path.name)
                images = extract_frames(input_path, input_dir, fps=max(0.1, float(job["fps"])))
                db.append_log(jid, f"Frames: {len(images)}")
                logger.info("job_id=%s stage=extract_frames_done frames=%s", jid, len(images))
            else:
                images = [input_path]
                logger.info("job_id=%s stage=image_ready file=%s", jid, input_path.name)
            db.set_total_items(jid, len(images))
            done_rows, pending_images = _split_completed_images(images, out_dir)
            done_before = len(done_rows)
            db.set_processed_items(jid, done_before)
            if done_before > 0:
                db.append_log(jid, f"Resuming with {done_before}/{len(images)} frame(s) already processed")
            try:
                min_det_conf = max(
                    0.0,
                    min(1.0, float(str(db.get_control("detector_min_confidence", "0.0") or "0.0"))),
                )
            except Exception:
                min_det_conf = 0.0
            suppress_blank_boxes = str(db.get_control("suppress_blank_species_boxes", "1") or "1").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            logger.info("job_id=%s stage=inference count=%s", jid, len(images))
            new_rows = process_images(
                pending_images,
                out_dir,
                ml_url=job.get("ml_url") or "http://127.0.0.1:8010",
                species_url=job.get("species_url") or "http://127.0.0.1:8100",
                progress_cb=lambda n, t, p: db.set_processed_items(jid, done_before + n),
                should_continue_cb=lambda: not db.is_cancelled(jid),
                min_detector_confidence=min_det_conf,
                suppress_blank_species_boxes=suppress_blank_boxes,
            )
            rows = done_rows + new_rows
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

