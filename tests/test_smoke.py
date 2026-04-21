"""Lightweight CI checks — no Docker services required."""

from fastapi.testclient import TestClient

from webapp.app import app


def test_root_returns_html() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
