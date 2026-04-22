from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class JobsDb:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._connect() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    fps REAL NOT NULL DEFAULT 1.0,
                    ml_url TEXT,
                    species_url TEXT,
                    total_items INTEGER NOT NULL DEFAULT 0,
                    processed_items INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    started_at TEXT,
                    finished_at TEXT,
                    output_dir TEXT,
                    outputs_json TEXT,
                    logs TEXT,
                    error_text TEXT
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS controls (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS frame_tags (
                    annotated_rel TEXT PRIMARY KEY,
                    tag_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            c.execute(
                "INSERT OR IGNORE INTO controls(key, value) VALUES ('paused', '0')"
            )
            cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
            if "ml_url" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN ml_url TEXT")
            if "species_url" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN species_url TEXT")
            if "total_items" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN total_items INTEGER NOT NULL DEFAULT 0")
            if "processed_items" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN processed_items INTEGER NOT NULL DEFAULT 0")
            c.commit()

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
        with self._connect() as c:
            existing = c.execute(
                """
                SELECT id, status FROM jobs
                WHERE input_path=? AND media_type=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (input_path, media_type),
            ).fetchone()
            if existing:
                ex_id = int(existing["id"])
                ex_status = str(existing["status"])
                if ex_status in ("queued", "running"):
                    return -ex_id
                c.execute(
                    """
                    UPDATE jobs
                    SET filename=?, fps=?, ml_url=?, species_url=?,
                        status='queued', started_at=NULL, finished_at=NULL,
                        output_dir=NULL, outputs_json=NULL, error_text=NULL,
                        total_items=0, processed_items=0
                    WHERE id=?
                    """,
                    (filename, fps, ml_url, species_url, ex_id),
                )
                c.commit()
                return ex_id
            cur = c.execute(
                """
                INSERT INTO jobs(filename, media_type, input_path, fps, ml_url, species_url, status, logs)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', '')
                """,
                (filename, media_type, input_path, fps, ml_url, species_url),
            )
            c.commit()
            return int(cur.lastrowid)

    def is_paused(self) -> bool:
        with self._connect() as c:
            row = c.execute(
                "SELECT value FROM controls WHERE key='paused'"
            ).fetchone()
            return (row["value"] if row else "0") == "1"

    def set_paused(self, paused: bool) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE controls SET value=? WHERE key='paused'",
                ("1" if paused else "0",),
            )
            c.commit()

    def get_control(self, key: str, default: str = "") -> str:
        with self._connect() as c:
            row = c.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            return str(row["value"])

    def set_control(self, key: str, value: str) -> None:
        with self._connect() as c:
            c.execute(
                """
                INSERT INTO controls(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
            c.commit()

    def fetch_next_queued(self) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def mark_running(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET status='running', started_at=datetime('now') WHERE id=?",
                (job_id,),
            )
            c.commit()

    def append_log(self, job_id: int, line: str) -> None:
        with self._connect() as c:
            row = c.execute("SELECT logs FROM jobs WHERE id=?", (job_id,)).fetchone()
            prev = row["logs"] if row and row["logs"] else ""
            c.execute(
                "UPDATE jobs SET logs=? WHERE id=?",
                (prev + line + "\n", job_id),
            )
            c.commit()

    def mark_done(self, job_id: int, output_dir: str, outputs: list[dict[str, str]]) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='done', finished_at=datetime('now'),
                    output_dir=?, outputs_json=?
                WHERE id=? AND status='running'
                """,
                (output_dir, json.dumps(outputs), job_id),
            )
            c.commit()

    def mark_error(self, job_id: int, error_text: str) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='error', finished_at=datetime('now'), error_text=?
                WHERE id=? AND status='running'
                """,
                (error_text[:4000], job_id),
            )
            c.commit()

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def fetch_all_jobs_for_source_summary(self) -> list[dict[str, Any]]:
        """All jobs, minimal columns — for Video / Source List (no row cap)."""
        with self._connect() as c:
            rows = c.execute(
                """
                SELECT filename, input_path, status,
                       COALESCE(total_items, 0) AS total_items,
                       COALESCE(processed_items, 0) AS processed_items
                FROM jobs
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def retry_job(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='queued', started_at=NULL, finished_at=NULL, error_text=NULL,
                    total_items=0, processed_items=0
                WHERE id=?
                """,
                (job_id,),
            )
            c.commit()

    def cancel_job(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='cancelled', finished_at=datetime('now')
                WHERE id=? AND status IN ('queued','running')
                """,
                (job_id,),
            )
            c.commit()

    def cancel_all_active(self) -> int:
        with self._connect() as c:
            cur = c.execute(
                """
                UPDATE jobs
                SET status='cancelled', finished_at=datetime('now')
                WHERE status IN ('queued','running')
                """
            )
            c.commit()
            return int(cur.rowcount or 0)

    def clear_all_jobs(self) -> int:
        with self._connect() as c:
            cur = c.execute("DELETE FROM jobs")
            c.commit()
            return int(cur.rowcount or 0)

    def has_running_jobs(self) -> bool:
        with self._connect() as c:
            row = c.execute(
                "SELECT 1 FROM jobs WHERE status='running' LIMIT 1"
            ).fetchone()
            return row is not None

    def is_cancelled(self, job_id: int) -> bool:
        with self._connect() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False
            return str(row["status"]) == "cancelled"

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def latest_job_for_input(self, input_path: str, media_type: str) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(
                """
                SELECT id, status, filename, finished_at
                FROM jobs
                WHERE input_path=? AND media_type=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (input_path, media_type),
            ).fetchone()
            return dict(row) if row else None

    def set_total_items(self, job_id: int, total: int) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET total_items=?, processed_items=0 WHERE id=?",
                (max(0, int(total)), job_id),
            )
            c.commit()

    def set_processed_items(self, job_id: int, processed: int) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET processed_items=? WHERE id=?",
                (max(0, int(processed)), job_id),
            )
            c.commit()

    def upsert_frame_tag(self, annotated_rel: str, tag_text: str) -> None:
        with self._connect() as c:
            c.execute(
                """
                INSERT INTO frame_tags(annotated_rel, tag_text, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(annotated_rel) DO UPDATE SET
                    tag_text=excluded.tag_text,
                    updated_at=datetime('now')
                """,
                (annotated_rel, tag_text),
            )
            c.commit()

    def remove_frame_tag(self, annotated_rel: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM frame_tags WHERE annotated_rel=?", (annotated_rel,))
            c.commit()

    def get_frame_tags_map(self) -> dict[str, str]:
        with self._connect() as c:
            rows = c.execute("SELECT annotated_rel, tag_text FROM frame_tags").fetchall()
            return {str(r["annotated_rel"]): str(r["tag_text"]) for r in rows}


def create_jobs_db(sqlite_path: Path) -> Any:
    """Create DB backend instance from environment configuration."""
    backend = (os.environ.get("DB_BACKEND") or "sqlite").strip().lower()
    if backend == "mongo":
        from webapp.jobs_db_mongo import MongoJobsDb

        mongo_uri = (os.environ.get("MONGO_URI") or "mongodb://127.0.0.1:27017").strip()
        mongo_db = (os.environ.get("MONGO_DB_NAME") or "wildlife_webapp").strip() or "wildlife_webapp"
        return MongoJobsDb(mongo_uri=mongo_uri, database_name=mongo_db)
    return JobsDb(sqlite_path)
