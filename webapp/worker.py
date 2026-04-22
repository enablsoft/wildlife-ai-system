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
            out_dir = output_dir / f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_job{jid}"
            out_dir.mkdir(parents=True, exist_ok=True)
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

