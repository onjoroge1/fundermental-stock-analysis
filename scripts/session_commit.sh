#!/bin/bash
# Auto-commit + push on Claude Code session end (SessionEnd hook).
# Safe by construction: no-ops when there is nothing to commit, when a
# merge/rebase is in progress, or when offline (push failure never blocks).
set -u
REPO="/Users/obadiah/Documents/stock-analyzer-2026"
cd "$REPO" || exit 0

# never interfere with an in-progress merge/rebase
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ] || [ -f .git/MERGE_HEAD ]; then
  echo '{"systemMessage": "auto-commit skipped: merge/rebase in progress"}'
  exit 0
fi

# nothing to do?
if [ -z "$(git status --porcelain)" ]; then
  exit 0
fi

git add -A
git commit -m "auto: session checkpoint $(date '+%Y-%m-%d %H:%M')" \
  -m "Automated end-of-session commit (SessionEnd hook)." >/dev/null 2>&1

if git push origin main >/dev/null 2>&1; then
  echo '{"systemMessage": "session auto-commit pushed to GitHub"}'
else
  echo '{"systemMessage": "session auto-commit saved locally; push failed (offline?) - will push next session"}'
fi
exit 0
