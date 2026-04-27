#!/usr/bin/env pwsh
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "=== Test Run ===" -ForegroundColor Cyan
& .\.venv\Scripts\Activate.ps1
pytest tests/ -q
if ($LASTEXITCODE -ne 0) { Write-Host "TESTS FAILING — fix before closing session" -ForegroundColor Red; exit 1 }
Write-Host "`n=== Git Status ===" -ForegroundColor Cyan
git status --short
Write-Host "`nReminder: update repo-memory.md and CHANGELOG.md before final commit." -ForegroundColor Yellow
Write-Host "Reminder: review active bugs / deferred bugs / pending decisions in repo-memory.md" -ForegroundColor Yellow
