<#
.SYNOPSIS
  Compare SQLite and Mongo metadata counts + key IDs.

.DESCRIPTION
  Performs lightweight sync validation between SQLite and Mongo:
  - row counts for jobs/controls/frame_tags
  - missing job IDs on each side
  - missing control keys and frame tag keys

  Exits non-zero when differences are found.

.PARAMETER SqlitePath
  Path to SQLite DB file. Default: data/webapp_jobs.sqlite

.PARAMETER MongoUri
  Mongo URI. Defaults to env MONGO_URI or localhost.

.PARAMETER DatabaseName
  Mongo database name. Defaults to env MONGO_DB_NAME or wildlife_webapp.
#>

param(
    [string]$SqlitePath = "data/webapp_jobs.sqlite",
    [string]$MongoUri = "",
    [string]$DatabaseName = ""
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
if (-not (Test-Path $resolvedSqlite)) {
    Write-Error "SQLite DB not found: $resolvedSqlite"
}

$pythonCode = @'
import json
import sqlite3
import sys
from pathlib import Path

from pymongo import MongoClient

sqlite_path = Path(sys.argv[1])
mongo_uri = sys.argv[2]
mongo_db_name = sys.argv[3]

con = sqlite3.connect(sqlite_path)
con.row_factory = sqlite3.Row

sqlite_jobs = {int(r[0]) for r in con.execute("SELECT id FROM jobs").fetchall()}
sqlite_controls = {str(r[0]) for r in con.execute("SELECT key FROM controls").fetchall()}
sqlite_tags = {str(r[0]) for r in con.execute("SELECT annotated_rel FROM frame_tags").fetchall()}

client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
client.admin.command("ping")
db = client[mongo_db_name]

mongo_jobs = {int(r.get("id")) for r in db["jobs"].find({}, {"_id": 0, "id": 1}) if r.get("id") is not None}
mongo_controls = {str(r.get("key")) for r in db["controls"].find({}, {"_id": 0, "key": 1}) if r.get("key")}
mongo_tags = {str(r.get("annotated_rel")) for r in db["frame_tags"].find({}, {"_id": 0, "annotated_rel": 1}) if r.get("annotated_rel")}

report = {
    "counts": {
        "sqlite": {"jobs": len(sqlite_jobs), "controls": len(sqlite_controls), "frame_tags": len(sqlite_tags)},
        "mongo": {"jobs": len(mongo_jobs), "controls": len(mongo_controls), "frame_tags": len(mongo_tags)},
    },
    "diff": {
        "jobs_missing_in_mongo": sorted(sqlite_jobs - mongo_jobs)[:50],
        "jobs_missing_in_sqlite": sorted(mongo_jobs - sqlite_jobs)[:50],
        "controls_missing_in_mongo": sorted(sqlite_controls - mongo_controls)[:50],
        "controls_missing_in_sqlite": sorted(mongo_controls - sqlite_controls)[:50],
        "tags_missing_in_mongo": sorted(sqlite_tags - mongo_tags)[:50],
        "tags_missing_in_sqlite": sorted(mongo_tags - sqlite_tags)[:50],
    },
}

has_diff = any(len(v) > 0 for v in report["diff"].values())
report["ok"] = not has_diff and report["counts"]["sqlite"] == report["counts"]["mongo"]
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["ok"] else 1)
'@

$tmpPy = Join-Path $env:TEMP ("check_sync_" + [guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $tmpPy -Value $pythonCode -Encoding UTF8

try {
    python $tmpPy $resolvedSqlite $MongoUri $DatabaseName
    exit $LASTEXITCODE
}
finally {
    Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
}
