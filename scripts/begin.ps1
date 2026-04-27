#!/usr/bin/env pwsh
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "=== Git Status ===" -ForegroundColor Cyan
git status --short
Write-Host "`n=== Last Commit ===" -ForegroundColor Cyan
git log -1 --oneline
Write-Host "`n=== Tests ===" -ForegroundColor Cyan
& .\.venv\Scripts\Activate.ps1
pytest tests/ -q
Write-Host "`n=== Last 30 lines of repo-memory.md ===" -ForegroundColor Cyan
Get-Content repo-memory.md -Tail 30
