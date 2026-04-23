#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)


def get_repo_slug(repo_root: Path, explicit_slug: str | None) -> str:
    if explicit_slug:
        return explicit_slug

    probe = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], repo_root)
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip()

    remote = run(["git", "remote", "get-url", "origin"], repo_root)
    if remote.returncode != 0:
        raise RuntimeError("Unable to resolve repository slug. Pass --repo owner/name.")

    raw = remote.stdout.strip()
    m = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/.]+)(?:\.git)?$", raw)
    if not m:
        raise RuntimeError(f"Could not parse GitHub slug from origin URL: {raw}")
    return m.group("slug")


def fetch_open_alerts(repo_root: Path, repo_slug: str) -> list[dict[str, Any]]:
    cp = run(
        ["gh", "api", f"repos/{repo_slug}/code-scanning/alerts?state=open&per_page=100"],
        repo_root,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "gh api call failed")
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse alert JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("Unexpected API response shape from code-scanning alerts endpoint.")
    return data


def print_alert_summary(alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        print("No open code-scanning alerts.")
        return
    print(f"Open code-scanning alerts: {len(alerts)}")
    for a in sorted(alerts, key=lambda x: int(x.get("number", 0))):
        n = a.get("number")
        rule = (a.get("rule") or {}).get("id", "unknown")
        sev = (a.get("rule") or {}).get("security_severity_level") or (a.get("rule") or {}).get("severity", "n/a")
        loc = (((a.get("most_recent_instance") or {}).get("location")) or {})
        path = loc.get("path", "?")
        line = loc.get("start_line", "?")
        print(f"- #{n} | {rule} | {sev} | {path}:{line}")


def _apply_redirect_fix(app_py: Path) -> int:
    if not app_py.exists():
        return 0
    src = app_py.read_text(encoding="utf-8")
    pat = re.compile(
        r'return RedirectResponse\(\s*url=f"/\?msg=\{.*?\}"\s*,\s*status_code=303\s*,?\s*\)',
        flags=re.DOTALL,
    )
    new_src, count = pat.subn('return RedirectResponse(url="/", status_code=303)', src)
    if count > 0 and new_src != src:
        app_py.write_text(new_src, encoding="utf-8")
    return count


def _apply_path_fix(routes_api_py: Path) -> int:
    if not routes_api_py.exists():
        return 0
    src = routes_api_py.read_text(encoding="utf-8")
    old = (
        "        if allowed_inputs and resolved.resolve(strict=False) not in allowed_inputs:\n"
        "            return JSONResponse({\"ok\": False, \"error\": \"Frame is not part of this job.\"}, status_code=403)\n"
    )
    new = (
        "        resolved_input = resolved.resolve(strict=False)\n"
        "        if not allowed_inputs:\n"
        "            return JSONResponse(\n"
        "                {\"ok\": False, \"error\": \"No recorded frames found for this job yet.\"},\n"
        "                status_code=409,\n"
        "            )\n"
        "        if resolved_input not in allowed_inputs:\n"
        "            return JSONResponse({\"ok\": False, \"error\": \"Frame is not part of this job.\"}, status_code=403)\n"
    )
    if old not in src:
        return 0
    routes_api_py.write_text(src.replace(old, new), encoding="utf-8")
    return 1


def apply_known_fixes(repo_root: Path) -> int:
    app_py = repo_root / "webapp" / "app.py"
    routes_api_py = repo_root / "webapp" / "routes_api.py"
    redirects = _apply_redirect_fix(app_py)
    pathfix = _apply_path_fix(routes_api_py)
    changed = redirects + pathfix
    print(f"Applied known fixes: redirect={redirects}, path-injection={pathfix}")
    return changed


def run_local_checks(repo_root: Path) -> int:
    flake = run(
        [
            sys.executable,
            "-m",
            "flake8",
            "webapp",
            "tests",
            "--count",
            "--select=E9,F63,F7,F82",
            "--show-source",
            "--statistics",
        ],
        repo_root,
    )
    print("\n[flake8 critical]")
    print((flake.stdout or "").strip() or "(no output)")
    if flake.returncode != 0:
        print((flake.stderr or "").strip())
        return flake.returncode

    pytest = run(
        [sys.executable, "-m", "pytest", "tests/test_ui_backend_functionality.py", "tests/test_worker_resume.py", "-q"],
        repo_root,
    )
    print("\n[pytest targeted]")
    print((pytest.stdout or "").strip() or "(no output)")
    if pytest.returncode != 0:
        print((pytest.stderr or "").strip())
    return pytest.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Code analysis smoke test: check GitHub alerts and optionally apply known local fixes."
    )
    parser.add_argument("--repo", help="GitHub repo slug in owner/name form. Auto-detected if omitted.")
    parser.add_argument("--apply-known-fixes", action="store_true", help="Apply known local patch templates.")
    parser.add_argument("--skip-local-checks", action="store_true", help="Skip flake8 and pytest smoke checks.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    try:
        slug = get_repo_slug(repo_root, args.repo)
        alerts = fetch_open_alerts(repo_root, slug)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    print(f"Repository: {slug}")
    print_alert_summary(alerts)

    if args.apply_known_fixes:
        apply_known_fixes(repo_root)

    if not args.skip_local_checks:
        return run_local_checks(repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
