"""Tests for scripts/code_analysis_fix.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_code_analysis_fix_module():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "code_analysis_fix.py"
    spec = importlib.util.spec_from_file_location("code_analysis_fix_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_skip_local_checks(monkeypatch, capsys) -> None:
    module = _load_code_analysis_fix_module()

    monkeypatch.setattr(module, "get_repo_slug", lambda _root, _repo: "enablsoft/wildlife-ai-system")
    monkeypatch.setattr(module, "fetch_open_alerts", lambda _root, _slug: [])
    monkeypatch.setattr(module.sys, "argv", ["code_analysis_fix.py", "--skip-local-checks"])

    rc = module.main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "Repository: enablsoft/wildlife-ai-system" in out
    assert "No open code-scanning alerts." in out


def test_apply_known_fixes_updates_expected_patterns(tmp_path: Path) -> None:
    module = _load_code_analysis_fix_module()

    webapp_dir = tmp_path / "webapp"
    webapp_dir.mkdir(parents=True, exist_ok=True)

    app_py = webapp_dir / "app.py"
    app_py.write_text(
        'return RedirectResponse(url=f"/?msg={quote_plus(msg)}", status_code=303)\n',
        encoding="utf-8",
    )

    routes_api_py = webapp_dir / "routes_api.py"
    routes_api_py.write_text(
        (
            "        if allowed_inputs and resolved.resolve(strict=False) not in allowed_inputs:\n"
            "            return JSONResponse({\"ok\": False, \"error\": \"Frame is not part of this job.\"}, status_code=403)\n"
        ),
        encoding="utf-8",
    )

    changed = module.apply_known_fixes(tmp_path)

    assert changed == 2
    assert 'return RedirectResponse(url="/", status_code=303)' in app_py.read_text(encoding="utf-8")
    routes_text = routes_api_py.read_text(encoding="utf-8")
    assert "resolved_input = resolved.resolve(strict=False)" in routes_text
    assert "No recorded frames found for this job yet." in routes_text
