import atexit
import os
import tempfile
from pathlib import Path

# Pytest and a local uvicorn both use the default SQLite path → GET / can block forever.
os.environ["WILDLIFE_WEBAPP_SKIP_WORKER"] = "1"
_fd, _SMOKE_DB = tempfile.mkstemp(prefix="wildlife_pytest_", suffix=".sqlite")
os.close(_fd)
os.environ["WILDLIFE_JOBS_DB"] = _SMOKE_DB
atexit.register(lambda: Path(_SMOKE_DB).unlink(missing_ok=True))

from fastapi.testclient import TestClient
from webapp.app import app


def test_root_returns_html() -> None:
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("content-type") or "")
