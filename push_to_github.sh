#!/usr/bin/env bash
# Saves all changes in this folder to GitHub (LibnThomas/Plantation-Management-System).
#   ./push_to_github.sh "what changed"
# Never sent: .env (passwords), databases, virtual environments, build output - see .gitignore.
set -euo pipefail
cd "$(dirname "$0")"
message="${1:-Update $(date '+%d/%m/%y %H:%M')}"
git add -A
if git diff --cached --quiet; then
  echo "Nothing new to push."
else
  git commit -m "$message"
fi
git pull --rebase origin master
git push origin master
echo "Pushed to https://github.com/LibnThomas/Plantation-Management-System"
