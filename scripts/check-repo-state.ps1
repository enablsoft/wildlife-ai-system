<#
.SYNOPSIS
  Check upstream Git status and local working tree.

.DESCRIPTION
  Prints a concise repo health report:
  - current branch and upstream
  - ahead/behind counts compared to upstream
  - local uncommitted file changes
  - optional open GitHub CodeQL alert summary

  This script is informational and does not fail by default.

.PARAMETER FailOnBehind
  Exit non-zero if local branch is behind upstream.

.PARAMETER IncludeCodeQL
  Query and print open GitHub CodeQL alerts for the current branch.

.PARAMETER AddressCodeQL
  When used with IncludeCodeQL, apply known local fix templates before printing alerts.

.PARAMETER EnsureGh
  Attempt to install GitHub CLI via winget when missing (default: enabled with IncludeCodeQL).
#>
param(
    [switch]$FailOnBehind,
    [switch]$IncludeCodeQL,
    [switch]$AddressCodeQL,
    [switch]$EnsureGh
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ==="
}

function Ensure-GhCli {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        return $true
    }
    Write-Host "GitHub CLI (gh) not found."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget unavailable. Install GitHub CLI manually: https://cli.github.com/"
        return $false
    }
    Write-Host "Attempting to install GitHub CLI via winget..."
    winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements | Out-Null
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        Write-Host "GitHub CLI installed."
        return $true
    }
    $ghPath = "C:\Program Files\GitHub CLI\gh.exe"
    if (Test-Path $ghPath) {
        Write-Host "GitHub CLI installed at $ghPath (new shells will pick it up on PATH)."
        return $true
    }
    Write-Host "Failed to detect GitHub CLI after install attempt."
    return $false
}

try {
    $insideRepo = (git rev-parse --is-inside-work-tree 2>$null)
    if ($LASTEXITCODE -ne 0 -or "$insideRepo".Trim() -ne "true") {
        Write-Host "Git status check skipped: current directory is not a git repository."
        return
    }

    $branch = (git branch --show-current 2>$null).Trim()
    $upstream = (git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null).Trim()
    if (-not $upstream) {
        $upstream = "origin/$branch"
    }

    Write-Section "Repository Check"
    Write-Host "Branch: $branch"
    Write-Host "Upstream: $upstream"

    git fetch --quiet --all 2>$null

    $counts = (git rev-list --left-right --count "$upstream...HEAD" 2>$null).Trim()
    if (-not $counts) {
        Write-Host "Could not compare against upstream. Verify remote and tracking branch."
    } else {
        $parts = $counts -split "\s+"
        $behind = [int]$parts[0]
        $ahead = [int]$parts[1]
        Write-Host "Remote delta: behind=$behind ahead=$ahead"

        if ($behind -gt 0) {
            Write-Host "Needs attention: local branch is behind upstream. Run 'git pull --rebase'."
            if ($FailOnBehind) {
                throw "Local branch is behind upstream."
            }
        }
        if ($ahead -gt 0) {
            Write-Host "Note: local branch has commits not on upstream. Consider pushing."
        }
    }

    Write-Section "Local Working Tree"
    $changes = @(git status --short)
    if (-not $changes -or $changes.Count -eq 0) {
        Write-Host "Working tree is clean."
    } else {
        Write-Host "Needs attention: uncommitted changes detected:"
        $changes | ForEach-Object { Write-Host "  $_" }
    }

    if ($IncludeCodeQL) {
        Write-Section "GitHub CodeQL"
        $canRunGh = $true
        if ($EnsureGh -or $IncludeCodeQL) {
            $canRunGh = Ensure-GhCli
        }
        if (-not $canRunGh) {
            Write-Host "Skipping CodeQL query because GitHub CLI is unavailable."
            return
        }
        $analysisArgs = @("scripts/code_analysis_fix.py", "--skip-local-checks")
        if ($branch) {
            $analysisArgs += @("--branch", $branch)
        }
        if ($AddressCodeQL) {
            $analysisArgs += "--apply-known-fixes"
            Write-Host "Address mode enabled: applying known local fix templates before alert listing."
        }
        python @analysisArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "CodeQL query step failed (exit $LASTEXITCODE). Ensure gh is installed/authenticated."
        }
    }
}
catch {
    Write-Host "Git status check failed: $($_.Exception.Message)"
}
