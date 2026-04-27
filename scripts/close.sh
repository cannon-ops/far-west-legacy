#!/bin/bash
cd "$(dirname "$0")/.."
echo "=== Test Run ==="
source .venv/bin/activate && pytest tests/ -q || { echo "TESTS FAILING — fix before closing session"; exit 1; }
echo
echo "=== Git Status ==="
git status --short
echo
echo "Reminder: update repo-memory.md and CHANGELOG.md before final commit."
echo "Reminder: review active bugs / deferred bugs / pending decisions in repo-memory.md"
