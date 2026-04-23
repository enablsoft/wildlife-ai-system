"""Backend smoke tests for SQLite and Mongo job stores."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from webapp.jobs_db import JobsDb, create_jobs_db


def _exercise_common_db_flow(db: object) -> None:
    """Run a minimal CRUD/status flow shared by both DB backends."""
    # typed as object to keep this test backend-agnostic
    jid = db.add_job(  # type: ignore[attr-defined]
        filename="demo.jpg",
        media_type="image",
        input_path="c:/tmp/demo.jpg",
        fps=1.0,
        ml_url="http://127.0.0.1:8010",
        species_url="http://127.0.0.1:8100",
    )
    assert isinstance(jid, int) and jid > 0

    queued = db.fetch_next_queued()  # type: ignore[attr-defined]
    assert queued and int(queued["id"]) == jid

    db.mark_running(jid)  # type: ignore[attr-defined]
    db.append_log(jid, "smoke line")  # type: ignore[attr-defined]
    db.set_total_items(jid, 3)  # type: ignore[attr-defined]
    db.set_processed_items(jid, 2)  # type: ignore[attr-defined]
    db.mark_done(jid, "test-media/output/run_001", [])  # type: ignore[attr-defined]

    row = db.get_job(jid)  # type: ignore[attr-defined]
    assert row and row.get("status") == "done"
    assert int(row.get("total_items") or 0) == 3
    assert int(row.get("processed_items") or 0) == 2

    db.set_control("runtime_output_dir", "test-media/output")  # type: ignore[attr-defined]
    assert db.get_control("runtime_output_dir") == "test-media/output"  # type: ignore[attr-defined]

    db.upsert_frame_tag("run_001/demo.annotated.jpg", "fox,night")  # type: ignore[attr-defined]
    tags = db.get_frame_tags_map()  # type: ignore[attr-defined]
    assert tags.get("run_001/demo.annotated.jpg") == "fox,night"
    db.remove_frame_tag("run_001/demo.annotated.jpg")  # type: ignore[attr-defined]
    assert "run_001/demo.annotated.jpg" not in db.get_frame_tags_map()  # type: ignore[attr-defined]

    removed = db.clear_all_jobs()  # type: ignore[attr-defined]
    assert isinstance(removed, int)
    jid2 = db.add_job(  # type: ignore[attr-defined]
        filename="fresh.jpg",
        media_type="image",
        input_path="c:/tmp/fresh.jpg",
        fps=1.0,
        ml_url="http://127.0.0.1:8010",
        species_url="http://127.0.0.1:8100",
    )
    assert int(jid2) == 1


def test_sqlite_backend_smoke(tmp_path: Path) -> None:
    """Validate SQLite JobsDb basic behavior."""
    sqlite_db = JobsDb(tmp_path / "webapp_jobs.sqlite")
    _exercise_common_db_flow(sqlite_db)


def test_sqlite_factory_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory returns SQLite backend when DB_BACKEND is unset."""
    monkeypatch.delenv("DB_BACKEND", raising=False)
    db = create_jobs_db(tmp_path / "factory.sqlite")
    assert isinstance(db, JobsDb)


def test_mongo_backend_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate Mongo backend basic behavior when Mongo is reachable."""
    pymongo = pytest.importorskip("pymongo")
    from webapp.jobs_db_mongo import MongoJobsDb

    mongo_uri = os.environ.get("MONGO_URI_TEST") or os.environ.get("MONGO_URI") or "mongodb://127.0.0.1:27017"
    db_name = f"wildlife_webapp_test_{uuid.uuid4().hex[:10]}"
    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"MongoDB not reachable for smoke test: {exc}")

    try:
        mongo_db = MongoJobsDb(mongo_uri=mongo_uri, database_name=db_name)
        _exercise_common_db_flow(mongo_db)

        monkeypatch.setenv("DB_BACKEND", "mongo")
        monkeypatch.setenv("MONGO_URI", mongo_uri)
        monkeypatch.setenv("MONGO_DB_NAME", db_name)
        factory_db = create_jobs_db(tmp_path / "unused.sqlite")
        assert isinstance(factory_db, MongoJobsDb)
    finally:
        client.drop_database(db_name)
