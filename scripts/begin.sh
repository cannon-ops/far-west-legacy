#!/bin/bash
cd "$(dirname "$0")/.."
echo "=== Git Status ==="
git status --short
echo
echo "=== Last Commit ==="
git log -1 --oneline
echo
echo "=== Tests ==="
source .venv/bin/activate 2>/dev/null && pytest tests/ -q 2>&1 | tail -3
echo
echo "=== Last 30 lines of repo-memory.md ==="
tail -30 repo-memory.md
