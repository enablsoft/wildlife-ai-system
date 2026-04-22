<#
.SYNOPSIS
  Preflight checks for configured database backend.

.DESCRIPTION
  Reads DB_BACKEND from env/.env (or uses -Backend override) and validates:
  - sqlite: DB file exists and expected tables are present
  - mongo: connectivity, required collections/indexes, and read/write probe

.PARAMETER Backend
  Optional explicit backend: sqlite or mongo.

.PARAMETER SqlitePath
  SQLite path for sqlite checks. Default: data/webapp_jobs.sqlite

.PARAMETER MongoUri
  Mongo URI for mongo checks. Defaults to MONGO_URI/env or localhost.

.PARAMETER DatabaseName
  Mongo DB name for mongo checks. Defaults to MONGO_DB_NAME/env or wildlife_webapp.
#>

param(
    [ValidateSet("sqlite", "mongo")]
    [string]$Backend = "",
    [string]$SqlitePath = "data/webapp_jobs.sqlite",
    [string]$MongoUri = "",
    [string]$DatabaseName = ""
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

if (-not $Backend) {
    $Backend = (Get-EnvValue -Key "DB_BACKEND" -DefaultValue "sqlite").Trim().ToLower()
}
if (-not $Backend) {
    $Backend = "sqlite"
}

if (-not $MongoUri) {
    $MongoUri = Get-EnvValue -Key "MONGO_URI" -DefaultValue "mongodb://127.0.0.1:27017"
}
if (-not $DatabaseName) {
    $DatabaseName = Get-EnvValue -Key "MONGO_DB_NAME" -DefaultValue "wildlife_webapp"
}

$resolvedSqlite = if ([System.IO.Path]::IsPathRooted($SqlitePath)) { $SqlitePath } else { Join-Path $repoRoot $SqlitePath }

$pythonCode = @'
import json
import sqlite3
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
backend = sys.argv[2].strip().lower()
sqlite_path = Path(sys.argv[3])
mongo_uri = sys.argv[4]
mongo_db_name = sys.argv[5]

sys.path.insert(0, str(repo_root))

result = {"backend": backend, "ok": True, "checks": []}

def add_check(name: str, ok: bool, details: str) -> None:
    result["checks"].append({"name": name, "ok": ok, "details": details})
    if not ok:
        result["ok"] = False

if backend == "sqlite":
    exists = sqlite_path.exists()
    add_check("sqlite_file_exists", exists, str(sqlite_path))
    if exists:
        con = sqlite3.connect(sqlite_path)
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = {str(r[0]) for r in rows}
        for t in ("jobs", "controls", "frame_tags"):
            add_check(f"sqlite_table_{t}", t in tables, f"tables={sorted(tables)}")
elif backend == "mongo":
    from pymongo import MongoClient
    from webapp.jobs_db_mongo import MongoJobsDb

    # Ensure schema/index setup.
    MongoJobsDb(mongo_uri=mongo_uri, database_name=mongo_db_name)
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[mongo_db_name]
    try:
        ping = client.admin.command("ping")
        add_check("mongo_ping", bool(ping.get("ok") == 1), str(ping))
    except Exception as e:
        add_check("mongo_ping", False, str(e))

    collections = set(db.list_collection_names())
    for c in ("jobs", "controls", "frame_tags"):
        add_check(f"mongo_collection_{c}", c in collections, f"collections={sorted(collections)}")

    probe_id = "__db_check_probe__"
    try:
        db["controls"].update_one({"key": probe_id}, {"$set": {"value": "ok"}}, upsert=True)
        row = db["controls"].find_one({"key": probe_id})
        add_check("mongo_write_read_probe", bool(row and row.get("value") == "ok"), str(row))
    except Exception as e:
        add_check("mongo_write_read_probe", False, str(e))
    finally:
        db["controls"].delete_one({"key": probe_id})
else:
    add_check("backend_supported", False, f"Unsupported backend: {backend}")

print(json.dumps(result, indent=2))
raise SystemExit(0 if result["ok"] else 1)
'@

$tmpPy = Join-Path $env:TEMP ("check_db_backend_" + [guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $tmpPy -Value $pythonCode -Encoding UTF8

try {
    python $tmpPy $repoRoot $Backend $resolvedSqlite $MongoUri $DatabaseName
    exit $LASTEXITCODE
}
finally {
    Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
}
