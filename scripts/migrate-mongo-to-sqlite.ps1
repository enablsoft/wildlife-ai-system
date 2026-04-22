<#
.SYNOPSIS
  Migrate webapp metadata from MongoDB to SQLite.

.DESCRIPTION
  Copies data from Mongo collections into SQLite tables:
  - jobs -> jobs
  - controls -> controls
  - frame_tags -> frame_tags

  The script supports dry-run mode and optional clearing of SQLite target tables.

.PARAMETER SqlitePath
  Path to SQLite DB file. Default: data/webapp_jobs.sqlite

.PARAMETER MongoUri
  Source Mongo URI. Defaults to env MONGO_URI or localhost.

.PARAMETER DatabaseName
  Source Mongo database name. Defaults to env MONGO_DB_NAME or wildlife_webapp.

.PARAMETER DryRun
  Read Mongo and report counts without writing to SQLite.

.PARAMETER ForceClearTarget
  Clear target SQLite tables before migration.
#>

param(
    [string]$SqlitePath = "data/webapp_jobs.sqlite",
    [string]$MongoUri = "",
    [string]$DatabaseName = "",
    [switch]$DryRun,
    [switch]$ForceClearTarget
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $MongoUri) {
    $MongoUri = if ($env:MONGO_URI) { $env:MONGO_URI } else { "mongodb://127.0.0.1:27017" }
}
if (-not $DatabaseName) {
    $DatabaseName = if ($env:MONGO_DB_NAME) { $env:MONGO_DB_NAME } else { "wildlife_webapp" }
}

$resolvedSqlite = if ([System.IO.Path]::IsPathRooted($SqlitePath)) { $SqlitePath } else { Join-Path $repoRoot $SqlitePath }

$pythonCode = @'
import json
import sqlite3
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
sqlite_path = Path(sys.argv[2])
mongo_uri = sys.argv[3]
mongo_db_name = sys.argv[4]
dry_run = sys.argv[5].strip().lower() == "true"
force_clear = sys.argv[6].strip().lower() == "true"

sys.path.insert(0, str(repo_root))

from pymongo import MongoClient
from webapp.jobs_db import JobsDb

client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
client.admin.command("ping")
mdb = client[mongo_db_name]

jobs = list(mdb["jobs"].find({}, {"_id": 0}))
controls = list(mdb["controls"].find({}, {"_id": 0}))
frame_tags = list(mdb["frame_tags"].find({}, {"_id": 0}))

summary = {
    "mongo_counts": {
        "jobs": len(jobs),
        "controls": len(controls),
        "frame_tags": len(frame_tags),
    },
    "sqlite_counts_before": {},
    "sqlite_counts_after": {},
    "dry_run": dry_run,
    "force_clear_target": force_clear,
}

db = JobsDb(sqlite_path)
con = sqlite3.connect(sqlite_path)
con.row_factory = sqlite3.Row
summary["sqlite_counts_before"] = {
    "jobs": int(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
    "controls": int(con.execute("SELECT COUNT(*) FROM controls").fetchone()[0]),
    "frame_tags": int(con.execute("SELECT COUNT(*) FROM frame_tags").fetchone()[0]),
}
con.close()

if dry_run:
    print(json.dumps(summary, indent=2))
    raise SystemExit(0)

con = sqlite3.connect(sqlite_path)
con.row_factory = sqlite3.Row
try:
    if force_clear:
        con.execute("DELETE FROM jobs")
        con.execute("DELETE FROM controls")
        con.execute("DELETE FROM frame_tags")

    for row in controls:
        key = str(row.get("key") or "")
        value = str(row.get("value") or "")
        if not key:
            continue
        con.execute(
            """
            INSERT INTO controls(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    for row in frame_tags:
        rel = str(row.get("annotated_rel") or "")
        tag = str(row.get("tag_text") or "")
        updated = str(row.get("updated_at") or "")
        if not rel:
            continue
        con.execute(
            """
            INSERT INTO frame_tags(annotated_rel, tag_text, updated_at)
            VALUES (?, ?, COALESCE(NULLIF(?, ''), datetime('now')))
            ON CONFLICT(annotated_rel) DO UPDATE SET
                tag_text=excluded.tag_text,
                updated_at=excluded.updated_at
            """,
            (rel, tag, updated),
        )

    job_cols = {
        "id", "filename", "media_type", "input_path", "fps", "ml_url", "species_url",
        "total_items", "processed_items", "status", "created_at", "started_at",
        "finished_at", "output_dir", "outputs_json", "logs", "error_text",
    }
    for row in jobs:
        payload = {k: row.get(k) for k in job_cols}
        if payload.get("id") is None:
            continue
        con.execute(
            """
            INSERT INTO jobs(
                id, filename, media_type, input_path, fps, ml_url, species_url,
                total_items, processed_items, status, created_at, started_at, finished_at,
                output_dir, outputs_json, logs, error_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filename=excluded.filename,
                media_type=excluded.media_type,
                input_path=excluded.input_path,
                fps=excluded.fps,
                ml_url=excluded.ml_url,
                species_url=excluded.species_url,
                total_items=excluded.total_items,
                processed_items=excluded.processed_items,
                status=excluded.status,
                created_at=excluded.created_at,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                output_dir=excluded.output_dir,
                outputs_json=excluded.outputs_json,
                logs=excluded.logs,
                error_text=excluded.error_text
            """,
            (
                int(payload.get("id")),
                str(payload.get("filename") or ""),
                str(payload.get("media_type") or ""),
                str(payload.get("input_path") or ""),
                float(payload.get("fps") or 1.0),
                str(payload.get("ml_url") or ""),
                str(payload.get("species_url") or ""),
                int(payload.get("total_items") or 0),
                int(payload.get("processed_items") or 0),
                str(payload.get("status") or "queued"),
                str(payload.get("created_at") or ""),
                payload.get("started_at"),
                payload.get("finished_at"),
                payload.get("output_dir"),
                payload.get("outputs_json"),
                str(payload.get("logs") or ""),
                payload.get("error_text"),
            ),
        )

    con.commit()
finally:
    con.close()

con = sqlite3.connect(sqlite_path)
summary["sqlite_counts_after"] = {
    "jobs": int(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
    "controls": int(con.execute("SELECT COUNT(*) FROM controls").fetchone()[0]),
    "frame_tags": int(con.execute("SELECT COUNT(*) FROM frame_tags").fetchone()[0]),
}
con.close()

print(json.dumps(summary, indent=2))
'@

$tmpPy = Join-Path $env:TEMP ("migrate_mongo_to_sqlite_" + [guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $tmpPy -Value $pythonCode -Encoding UTF8

try {
    python $tmpPy $repoRoot $resolvedSqlite $MongoUri $DatabaseName $DryRun $ForceClearTarget
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Migration failed."
    }
}
finally {
    Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
}
