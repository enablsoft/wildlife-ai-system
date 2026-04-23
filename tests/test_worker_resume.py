from __future__ import annotations

import json
from pathlib import Path

from webapp.worker import run_worker_loop


class _FakeDb:
    def __init__(self, job: dict[str, object]) -> None:
        self._job = dict(job)
        self._queue_emitted = False
        self.done_rows: list[dict[str, str]] | None = None
        self.done_out_dir = ""
        self.status = "queued"
        self.total_items = 0
        self.processed_items = 0
        self.logs: list[str] = []

    def is_paused(self) -> bool:
        return False

    def fetch_next_queued(self) -> dict[str, object] | None:
        if self._queue_emitted:
            return None
        self._queue_emitted = True
        return self._job

    def mark_running(self, job_id: int) -> None:
        self.status = "running"

    def get_control(self, key: str, default: str = "") -> str:
        return default

    def set_output_dir(self, job_id: int, output_dir: str) -> None:
        self._job["output_dir"] = output_dir

    def append_log(self, job_id: int, line: str) -> None:
        self.logs.append(line)

    def set_total_items(self, job_id: int, total: int) -> None:
        self.total_items = int(total)

    def set_processed_items(self, job_id: int, processed: int) -> None:
        self.processed_items = int(processed)

    def is_cancelled(self, job_id: int) -> bool:
        return False

    def mark_done(self, job_id: int, output_dir: str, outputs: list[dict[str, str]]) -> None:
        self.status = "done"
        self.done_out_dir = output_dir
        self.done_rows = outputs

    def mark_error(self, job_id: int, error_text: str) -> None:
        self.status = "error"


def _make_completed_artifacts(output_dir: Path, frame_path: Path) -> None:
    stem = frame_path.stem
    (output_dir / f"{stem}.ml.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (output_dir / f"{stem}.species.json").write_text(json.dumps({"prediction": "fox"}), encoding="utf-8")
    (output_dir / f"{stem}.annotated.jpg").write_bytes(b"jpg")


def test_worker_resume_skips_already_processed_frames(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    frames = [input_dir / "video_frame_0001.jpg", input_dir / "video_frame_0002.jpg", input_dir / "video_frame_0003.jpg"]
    for f in frames:
        f.write_bytes(b"frame")

    prior_run = output_dir / "run_20260101_000000_job10"
    prior_run.mkdir()
    _make_completed_artifacts(prior_run, frames[0])

    processed_inputs: list[str] = []

    def fake_extract_frames(_video: Path, _frames_dir: Path, fps: float = 1.0) -> list[Path]:
        return frames

    def fake_process_images(
        images: list[Path],
        out_dir: Path,
        ml_url: str,
        species_url: str,
        progress_cb=None,
        should_continue_cb=None,
        min_detector_confidence: float = 0.7,
        suppress_blank_species_boxes: bool = True,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for idx, img in enumerate(images, start=1):
            processed_inputs.append(img.name)
            _make_completed_artifacts(out_dir, img)
            rows.append(
                {
                    "input": str(img),
                    "ml_json": str(out_dir / f"{img.stem}.ml.json"),
                    "species_json": str(out_dir / f"{img.stem}.species.json"),
                    "annotated": str(out_dir / f"{img.stem}.annotated.jpg"),
                }
            )
            if progress_cb is not None:
                progress_cb(idx, len(images), img)
        return rows

    db = _FakeDb(
        {
            "id": 10,
            "filename": "video.mp4",
            "media_type": "video",
            "input_path": str(video_path),
            "output_dir": str(prior_run),
            "fps": 1.0,
            "ml_url": "http://127.0.0.1:8010",
            "species_url": "http://127.0.0.1:8100",
        }
    )

    tick = {"n": 0}

    def is_stopped() -> bool:
        tick["n"] += 1
        return db.status == "done" or tick["n"] > 6

    monkeypatch.setattr("webapp.worker.time.sleep", lambda _: None)

    run_worker_loop(
        is_stopped=is_stopped,
        db=db,
        logger=type("_L", (), {"info": lambda *a, **k: None, "exception": lambda *a, **k: None})(),
        get_runtime_dirs=lambda: (input_dir, tmp_path / "video_store", output_dir),
        extract_frames=fake_extract_frames,
        process_images=fake_process_images,
    )

    assert db.status == "done"
    assert db.done_out_dir == str(prior_run)
    assert processed_inputs == ["video_frame_0002.jpg", "video_frame_0003.jpg"]
    assert db.total_items == 3
    assert db.processed_items == 3
    assert db.done_rows is not None and len(db.done_rows) == 3
