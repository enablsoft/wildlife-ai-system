"""Backend functionality checks for UI-triggered actions."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import webapp.app as app_module


def test_ui_button_action_routes_work(monkeypatch, tmp_path: Path) -> None:
    """Exercise main UI button routes and verify backend wiring."""
    calls: list[tuple[str, object]] = []
    controls: dict[str, str] = {}

    # Patch DB interactions so tests stay deterministic.
    monkeypatch.setattr(app_module.db, "set_paused", lambda v: calls.append(("set_paused", v)))
    monkeypatch.setattr(app_module.db, "retry_job", lambda job_id: calls.append(("retry_job", job_id)))
    monkeypatch.setattr(app_module.db, "cancel_job", lambda job_id: calls.append(("cancel_job", job_id)))
    monkeypatch.setattr(app_module.db, "cancel_all_active", lambda: 2)
    monkeypatch.setattr(app_module.db, "has_running_jobs", lambda: False)
    monkeypatch.setattr(app_module.db, "clear_all_jobs", lambda: 3)
    monkeypatch.setattr(app_module.db, "list_jobs", lambda limit=500: [])
    monkeypatch.setattr(app_module.db, "get_job", lambda _job_id: None)
    monkeypatch.setattr(app_module.db, "set_control", lambda k, v: controls.__setitem__(k, str(v)))
    monkeypatch.setattr(app_module.db, "list_all_jobs", lambda: [])

    in_dir = tmp_path / "input"
    vid_dir = tmp_path / "video"
    out_dir = tmp_path / "output"
    in_dir.mkdir(parents=True, exist_ok=True)
    vid_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_001").mkdir(parents=True, exist_ok=True)
    (in_dir / "frame_001.jpg").write_bytes(b"x")
    (vid_dir / "video_001.mp4").write_bytes(b"x")
    monkeypatch.setattr(app_module, "_runtime_dirs", lambda: (in_dir, vid_dir, out_dir))

    client = TestClient(app_module.app)

    for path in (
        "/pause",
        "/resume",
        "/retry/11",
        "/cancel/12",
        "/cancel-all",
        "/clear-jobs",
        "/cleanup-output",
        "/reset-generated-media",
        "/reset-all",
        "/open-output/999",
    ):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303, path

    assert ("set_paused", True) in calls
    assert ("set_paused", False) in calls
    assert ("retry_job", 11) in calls
    assert ("cancel_job", 12) in calls


def test_ui_backend_api_endpoints_work(monkeypatch, tmp_path: Path) -> None:
    """Exercise API endpoints used by settings/export and folder queue buttons."""
    controls: dict[str, str] = {}
    tag_updates: list[tuple[str, str]] = []
    queued: list[tuple[str, str]] = []

    monkeypatch.setattr(app_module.db, "set_control", lambda k, v: controls.__setitem__(k, str(v)))
    monkeypatch.setattr(app_module.db, "list_all_jobs", lambda: [])
    monkeypatch.setattr(app_module.db, "latest_job_for_input", lambda _p, _t: None)
    monkeypatch.setattr(
        app_module.db,
        "add_job",
        lambda filename, media_type, input_path, fps, ml_url, species_url: (
            queued.append((filename, media_type)) or 123
        ),
    )
    monkeypatch.setattr(app_module.db, "upsert_frame_tag", lambda rel, tag: tag_updates.append((rel, tag)))
    monkeypatch.setattr(app_module.db, "remove_frame_tag", lambda rel: tag_updates.append((rel, "")))

    folder = tmp_path / "batch"
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "demo.jpg"
    image.write_bytes(b"jpg")

    client = TestClient(app_module.app)

    settings_resp = client.post(
        "/api/settings/runtime",
        json={
            "input_dir": str(tmp_path / "in"),
            "video_dir": str(tmp_path / "vid"),
            "output_dir": str(tmp_path / "out"),
        },
    )
    assert settings_resp.status_code == 200
    assert settings_resp.json().get("ok") is True
    assert "runtime_output_dir" in controls

    preview_resp = client.post(
        "/api/enqueue-folder-preview",
        json={"folder_path": str(folder), "exts": ".jpg,.png"},
    )
    assert preview_resp.status_code == 200
    payload = preview_resp.json()
    assert payload.get("ok") is True
    assert len(payload.get("items") or []) == 1

    commit_resp = client.post(
        "/api/enqueue-folder-commit",
        json={
            "folder_path": str(folder),
            "exts": ".jpg,.png",
            "fps": 1.0,
            "ml_url": "http://127.0.0.1:8010",
            "species_url": "http://127.0.0.1:8100",
            "input_paths": [str(image).lower()],
        },
    )
    assert commit_resp.status_code == 200
    assert commit_resp.json().get("ok") is True
    assert queued

    tag_resp = client.post("/api/frame-tag", json={"annotated_rel": "a/b.jpg", "tag_text": "fox, night"})
    assert tag_resp.status_code == 200
    assert tag_resp.json().get("ok") is True
    assert ("a/b.jpg", "fox, night") in tag_updates

    export_resp = client.get("/export/frame-results.xlsx")
    assert export_resp.status_code in (200, 500)
    if export_resp.status_code == 200:
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in export_resp.headers.get(
            "content-type", ""
        )
    else:
        # In minimal environments, export can fail if openpyxl is missing.
        payload = export_resp.json()
        assert payload.get("ok") is False
        assert "openpyxl" in str(payload.get("error", "")).lower()

