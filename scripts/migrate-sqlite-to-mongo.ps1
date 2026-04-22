<#
.SYNOPSIS
  Migrate webapp metadata from SQLite to MongoDB.

.DESCRIPTION
  Copies data from SQLite tables into Mongo collections:
  - jobs -> jobs
  - controls -> controls
  - frame_tags -> frame_tags

  The script is idempotent (upserts by logical keys) and can run in dry-run mode.

.PARAMETER SqlitePath
  Path to SQLite DB file. Default: data/webapp_jobs.sqlite

.PARAMETER MongoUri
  Target Mongo URI. Defaults to env MONGO_URI or localhost.

.PARAMETER DatabaseName
  Target Mongo database name. Defaults to env MONGO_DB_NAME or wildlife_webapp.

.PARAMETER DryRun
  Read SQLite and report migration counts without writing to Mongo.

.PARAMETER ForceClearTarget
  Clear target Mongo collections before migration.

.PARAMETER AllowWhileMongoActive
  Allow migration even when DB_BACKEND is already set to mongo.
#>

param(
    [string]$SqlitePath = "data/webapp_jobs.sqlite",
    [string]$MongoUri = "",
    [string]$DatabaseName = "",
    [switch]$DryRun,
    [switch]$ForceClearTarget,
    [switch]$AllowWhileMongoActive
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Get-EnvValue {
    param(
        [string]$Key,
        [string]$DefaultValue = ""
    )
    $envValue = [Environment]::GetEnvironmentVariable($Key)
    if ($envValue) {
        return [string]$envValue
    }
    $envFile = Join-Path $repoRoot ".env"
    if (-not (Test-Path $envFile)) {
        return $DefaultValue
    }
    $line = Select-String -Path $envFile -Pattern ("^\s*" + [regex]::Escape($Key) + "\s*=") | Select-Object -First 1
    if (-not $line) {
        return $DefaultValue
    }
    $raw = ($line.Line -split "=", 2)[1].Trim()
    return $raw.Trim("'`"")
}

$configuredBackend = (Get-EnvValue -Key "DB_BACKEND" -DefaultValue "sqlite").Trim().ToLower()
if (-not $configuredBackend) {
    $configuredBackend = "sqlite"
}
if (($configuredBackend -eq "mongo") -and (-not $AllowWhileMongoActive)) {
    Write-Error "Blocked migration: DB_BACKEND is currently 'mongo'. Switch to sqlite first or pass -AllowWhileMongoActive."
}

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

repo_root = Path(sys.argv[1])
sqlite_path = Path(sys.argv[2])
mongo_uri = sys.argv[3]
mongo_db_name = sys.argv[4]
dry_run = sys.argv[5].strip().lower() == "true"
force_clear = sys.argv[6].strip().lower() == "true"

sys.path.insert(0, str(repo_root))

def normalize_row(row):
    out = dict(row)
    out.pop("_id", None)
    return out

con = sqlite3.connect(sqlite_path)
con.row_factory = sqlite3.Row

jobs = [dict(r) for r in con.execute("SELECT * FROM jobs").fetchall()]
controls = [dict(r) for r in con.execute("SELECT key, value FROM controls").fetchall()]
frame_tags = [dict(r) for r in con.execute("SELECT annotated_rel, tag_text, updated_at FROM frame_tags").fetchall()]

summary = {
    "sqlite_counts": {
        "jobs": len(jobs),
        "controls": len(controls),
        "frame_tags": len(frame_tags),
    },
    "mongo_counts_before": {},
    "mongo_counts_after": {},
    "dry_run": dry_run,
    "force_clear_target": force_clear,
}

if dry_run:
    print(json.dumps(summary, indent=2))
    raise SystemExit(0)

from pymongo import MongoClient
from webapp.jobs_db_mongo import MongoJobsDb

# Initialize indexes/collections in target DB.
mongo_store = MongoJobsDb(mongo_uri=mongo_uri, database_name=mongo_db_name)
client = MongoClient(mongo_uri)
db = client[mongo_db_name]

summary["mongo_counts_before"] = {
    "jobs": db["jobs"].count_documents({}),
    "controls": db["controls"].count_documents({}),
    "frame_tags": db["frame_tags"].count_documents({}),
}

if force_clear:
    db["jobs"].delete_many({})
    db["controls"].delete_many({})
    db["frame_tags"].delete_many({})

for row in controls:
    db["controls"].update_one({"key": row["key"]}, {"$set": {"value": str(row.get("value") or "")}}, upsert=True)

for row in frame_tags:
    db["frame_tags"].update_one(
        {"annotated_rel": str(row.get("annotated_rel") or "")},
        {"$set": {
            "tag_text": str(row.get("tag_text") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }},
        upsert=True,
    )

max_job_id = 0
for row in jobs:
    doc = normalize_row(row)
    job_id = int(doc.get("id") or 0)
    max_job_id = max(max_job_id, job_id)
    db["jobs"].update_one({"id": job_id}, {"$set": doc}, upsert=True)

# Keep auto-increment counter in sync.
if max_job_id > 0:
    db["counters"].update_one({"_id": "job_id"}, {"$max": {"seq": max_job_id}}, upsert=True)

summary["mongo_counts_after"] = {
    "jobs": db["jobs"].count_documents({}),
    "controls": db["controls"].count_documents({}),
    "frame_tags": db["frame_tags"].count_documents({}),
}

print(json.dumps(summary, indent=2))
'@

$tmpPy = Join-Path $env:TEMP ("migrate_sqlite_to_mongo_" + [guid]::NewGuid().ToString("N") + ".py")
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
