from __future__ import annotations

import json
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
                "INSERT OR IGNORE INTO controls(key, value) VALUES ('paused', '0')"
            )
            cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
            if "ml_url" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN ml_url TEXT")
            if "species_url" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN species_url TEXT")
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
                WHERE id=?
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
                WHERE id=?
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

    def retry_job(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute(
                """
                UPDATE jobs
                SET status='queued', started_at=NULL, finished_at=NULL, error_text=NULL
                WHERE id=?
                """,
                (job_id,),
            )
            c.commit()

    def cancel_job(self, job_id: int) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE jobs SET status='cancelled', finished_at=datetime('now') WHERE id=? AND status='queued'",
                (job_id,),
            )
            c.commit()
