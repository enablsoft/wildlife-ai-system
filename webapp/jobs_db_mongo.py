from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class MongoJobsDb:
    """Mongo-backed implementation of the webapp job store."""

    def __init__(self, mongo_uri: str, database_name: str) -> None:
        self.client = MongoClient(mongo_uri)
        self.db: Database[Any] = self.client[database_name]
        self.jobs: Collection[Any] = self.db["jobs"]
        self.controls: Collection[Any] = self.db["controls"]
        self.frame_tags: Collection[Any] = self.db["frame_tags"]
        self.counters: Collection[Any] = self.db["counters"]
        self._init()

    def _init(self) -> None:
        self.jobs.create_index([("id", ASCENDING)], unique=True)
        self.jobs.create_index([("status", ASCENDING), ("id", ASCENDING)])
        self.jobs.create_index([("input_path", ASCENDING), ("media_type", ASCENDING), ("id", DESCENDING)])
        self.controls.create_index([("key", ASCENDING)], unique=True)
        self.frame_tags.create_index([("annotated_rel", ASCENDING)], unique=True)
        self.controls.update_one(
            {"key": "paused"},
            {"$setOnInsert": {"value": "0"}},
            upsert=True,
        )

    def _next_job_id(self) -> int:
        row = self.counters.find_one_and_update(
            {"_id": "job_id"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
        )
        return int((row or {}).get("seq") or 1)

    @staticmethod
    def _row(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if not doc:
            return None
        out = dict(doc)
        out.pop("_id", None)
        return out

    def add_job(
        self,
        *,
        filename: str,
        media_type: str,
        input_path: str,
        fps: float,
        ml_url: str,
        species_url: str,
    ) -> int:
        existing = self.jobs.find_one(
            {"input_path": input_path, "media_type": media_type},
            sort=[("id", DESCENDING)],
        )
        if existing:
            ex_id = int(existing["id"])
            ex_status = str(existing.get("status") or "")
            if ex_status in ("queued", "running"):
                return -ex_id
            self.jobs.update_one(
                {"id": ex_id},
                {
                    "$set": {
                        "filename": filename,
                        "fps": float(fps),
                        "ml_url": ml_url,
                        "species_url": species_url,
                        "status": "queued",
                        "started_at": None,
                        "finished_at": None,
                        "output_dir": None,
                        "outputs_json": None,
                        "error_text": None,
                        "total_items": 0,
                        "processed_items": 0,
                    }
                },
            )
            return ex_id

        job_id = self._next_job_id()
        doc = {
            "id": job_id,
            "filename": filename,
            "media_type": media_type,
            "input_path": input_path,
            "fps": float(fps),
            "ml_url": ml_url,
            "species_url": species_url,
            "total_items": 0,
            "processed_items": 0,
            "status": "queued",
            "created_at": _utc_now_str(),
            "started_at": None,
            "finished_at": None,
            "output_dir": None,
            "outputs_json": None,
            "logs": "",
            "error_text": None,
        }
        try:
            self.jobs.insert_one(doc)
        except DuplicateKeyError:
            return self.add_job(
                filename=filename,
                media_type=media_type,
                input_path=input_path,
                fps=fps,
                ml_url=ml_url,
                species_url=species_url,
            )
        return job_id

    def is_paused(self) -> bool:
        row = self.controls.find_one({"key": "paused"})
        return str((row or {}).get("value", "0")) == "1"

    def set_paused(self, paused: bool) -> None:
        self.controls.update_one({"key": "paused"}, {"$set": {"value": "1" if paused else "0"}}, upsert=True)

    def get_control(self, key: str, default: str = "") -> str:
        row = self.controls.find_one({"key": key})
        if not row:
            return default
        return str(row.get("value") or "")

    def set_control(self, key: str, value: str) -> None:
        self.controls.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

    def fetch_next_queued(self) -> dict[str, Any] | None:
        return self._row(self.jobs.find_one({"status": "queued"}, sort=[("id", ASCENDING)]))

    def mark_running(self, job_id: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {"$set": {"status": "running", "started_at": _utc_now_str()}},
        )

    def set_output_dir(self, job_id: int, output_dir: str) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {"$set": {"output_dir": output_dir}},
        )

    def append_log(self, job_id: int, line: str) -> None:
        row = self.jobs.find_one({"id": int(job_id)}, {"logs": 1})
        prev = str((row or {}).get("logs") or "")
        self.jobs.update_one({"id": int(job_id)}, {"$set": {"logs": prev + line + "\n"}})

    def mark_done(self, job_id: int, output_dir: str, outputs: list[dict[str, str]]) -> None:
        self.jobs.update_one(
            {"id": int(job_id), "status": "running"},
            {
                "$set": {
                    "status": "done",
                    "finished_at": _utc_now_str(),
                    "output_dir": output_dir,
                    "outputs_json": json.dumps(outputs),
                }
            },
        )

    def mark_error(self, job_id: int, error_text: str) -> None:
        self.jobs.update_one(
            {"id": int(job_id), "status": "running"},
            {"$set": {"status": "error", "finished_at": _utc_now_str(), "error_text": error_text[:4000]}},
        )

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.jobs.find({}, sort=[("id", DESCENDING)]).limit(int(limit))
        return [self._row(r) or {} for r in rows]

    def list_all_jobs(self) -> list[dict[str, Any]]:
        rows = self.jobs.find({}, sort=[("id", DESCENDING)])
        return [self._row(r) or {} for r in rows]

    def fetch_all_jobs_for_source_summary(self) -> list[dict[str, Any]]:
        rows = self.jobs.find(
            {},
            {
                "_id": 0,
                "filename": 1,
                "input_path": 1,
                "status": 1,
                "total_items": 1,
                "processed_items": 1,
            },
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "filename": r.get("filename"),
                    "input_path": r.get("input_path"),
                    "status": r.get("status"),
                    "total_items": int(r.get("total_items") or 0),
                    "processed_items": int(r.get("processed_items") or 0),
                }
            )
        return out

    def retry_job(self, job_id: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {
                "$set": {
                    "status": "queued",
                    "started_at": None,
                    "finished_at": None,
                    "error_text": None,
                    "output_dir": None,
                    "outputs_json": None,
                    "total_items": 0,
                    "processed_items": 0,
                }
            },
        )

    def resume_job(self, job_id: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {
                "$set": {
                    "status": "queued",
                    "started_at": None,
                    "finished_at": None,
                    "error_text": None,
                }
            },
        )

    def cancel_job(self, job_id: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id), "status": {"$in": ["queued", "running"]}},
            {"$set": {"status": "cancelled", "finished_at": _utc_now_str()}},
        )

    def cancel_all_active(self) -> int:
        result = self.jobs.update_many(
            {"status": {"$in": ["queued", "running"]}},
            {"$set": {"status": "cancelled", "finished_at": _utc_now_str()}},
        )
        return int(result.modified_count)

    def clear_all_jobs(self) -> int:
        result = self.jobs.delete_many({})
        # Reset job id sequence so fresh runs start at job #1 after full reset.
        self.counters.update_one({"_id": "job_id"}, {"$set": {"seq": 0}}, upsert=True)
        return int(result.deleted_count)

    def has_running_jobs(self) -> bool:
        return self.jobs.find_one({"status": "running"}, {"_id": 1}) is not None

    def is_cancelled(self, job_id: int) -> bool:
        row = self.jobs.find_one({"id": int(job_id)}, {"status": 1})
        if not row:
            return False
        return str(row.get("status") or "") == "cancelled"

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return self._row(self.jobs.find_one({"id": int(job_id)}))

    def latest_job_for_input(self, input_path: str, media_type: str) -> dict[str, Any] | None:
        row = self.jobs.find_one(
            {"input_path": input_path, "media_type": media_type},
            {"_id": 0, "id": 1, "status": 1, "filename": 1, "finished_at": 1},
            sort=[("id", DESCENDING)],
        )
        return dict(row) if row else None

    def set_total_items(self, job_id: int, total: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {"$set": {"total_items": max(0, int(total)), "processed_items": 0}},
        )

    def set_processed_items(self, job_id: int, processed: int) -> None:
        self.jobs.update_one(
            {"id": int(job_id)},
            {"$set": {"processed_items": max(0, int(processed))}},
        )

    def upsert_output_row(self, job_id: int, row: dict[str, str]) -> None:
        """Insert or replace one output artifact row for a job."""
        doc = self.jobs.find_one(
            {"id": int(job_id)},
            {"outputs_json": 1, "processed_items": 1, "total_items": 1},
        )
        if not doc:
            return
        raw = doc.get("outputs_json") or "[]"
        try:
            outputs = json.loads(str(raw))
        except Exception:
            outputs = []
        if not isinstance(outputs, list):
            outputs = []
        key_in = str(row.get("input") or "")
        key_ann = str(row.get("annotated") or "")
        replaced = False
        for idx, item in enumerate(outputs):
            if not isinstance(item, dict):
                continue
            same_input = str(item.get("input") or "") == key_in and key_in
            same_ann = str(item.get("annotated") or "") == key_ann and key_ann
            if same_input or same_ann:
                outputs[idx] = row
                replaced = True
                break
        if not replaced:
            outputs.append(row)
        total = int(doc.get("total_items") or 0)
        processed = int(doc.get("processed_items") or 0)
        if total <= 0:
            total = len(outputs)
        processed = min(max(processed, len(outputs)), total)
        self.jobs.update_one(
            {"id": int(job_id)},
            {
                "$set": {
                    "outputs_json": json.dumps(outputs),
                    "processed_items": processed,
                    "total_items": total,
                }
            },
        )

    def upsert_frame_tag(self, annotated_rel: str, tag_text: str) -> None:
        self.frame_tags.update_one(
            {"annotated_rel": annotated_rel},
            {"$set": {"tag_text": tag_text, "updated_at": _utc_now_str()}},
            upsert=True,
        )

    def remove_frame_tag(self, annotated_rel: str) -> None:
        self.frame_tags.delete_one({"annotated_rel": annotated_rel})

    def get_frame_tags_map(self) -> dict[str, str]:
        rows = self.frame_tags.find({}, {"_id": 0, "annotated_rel": 1, "tag_text": 1})
        return {str(r.get("annotated_rel") or ""): str(r.get("tag_text") or "") for r in rows}
