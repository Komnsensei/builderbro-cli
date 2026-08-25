#!/usr/bin/env bash
#
# Push all the locally-committed repo fixes to GitHub.
#
# Prereq: log in to GitHub first:
#   gh auth login
#   (or: git config --global credential.helper store  then push once manually)
#
# Usage:
#   bash push-all.sh
#
set -euo pipefail

cd "$(dirname "$0")"

push_repo() {
  local repo="$1"
  echo "==> Pushing $repo"
  git -C "$repo" push origin main
}

push_repo FESTiViLLAN     # untrack .env.local + .gitignore + MIT license
push_repo app             # MIT license
push_repo cinemaCRAFT     # MIT license
push_repo builderBRO      # MIT license
push_repo qrbtc-api       # MIT license
push_repo passioncraft-vercel # MIT license
push_repo newstate-fullstack  # MIT license

echo ""
echo "==> All pushed. Remaining by hand:"
echo "  1. Quantumpass: bash quantumpass-lfs-migrate.sh  (rewrites history, force push)"
echo "  2. NewState:    commit + push kernel/kernel.cjs, kernel/bro-agent.cjs,"
echo "                  kernel/qih-monitor.cjs, closed_loop_graph_pruner.py,"
echo "                  engine/server.cjs fixes, LICENSE, .github/workflows/ci.yml"
