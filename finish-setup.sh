#!/bin/bash
# Grants the `workflow` scope, pushes the workflow, and starts the watch.
set -e
cd "$(dirname "$0")"
echo ">>> Granting workflow scope (opens your browser)..."
gh auth refresh -h github.com -s workflow
echo ">>> Pushing workflow..."
git add .github/workflows/seat-watch.yml
git -c user.email="fmendezc@yahoo.com.mx" -c user.name="fmendezc98" \
    commit -q -m "Add seat-watch workflow"
git push -q origin main
echo ">>> Starting the watch now..."
gh workflow run seat-watch.yml
sleep 8
gh run list --workflow=seat-watch.yml --limit 3
echo ">>> Done. Follow it with: gh run watch"
