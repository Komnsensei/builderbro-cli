#!/usr/bin/env bash
#
# Quantumpass: move committed MP4s to Git LFS + add MIT license.
#
# WHY: the repo is ~140 MB on GitHub because dozens of MP4 files
# (public/*.mp4, src/assets/*.mp4, ~275 MB of blobs) are committed directly.
#
# WARNING: this REWRITES GIT HISTORY (all commit SHAs change) and requires a
# FORCE PUSH. Only run it if you own the repo and have no collaborators whose
# clones you care about. Run after `gh auth login`.
#
# Usage:
#   bash quantumpass-lfs-migrate.sh
#
set -euo pipefail

REPO_URL="https://github.com/Komnsensei/Quantumpass.git"
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
CLONE_DIR="$WORK_DIR/Quantumpass"

if ! command -v git-lfs >/dev/null 2>&1; then
  echo "ERROR: git-lfs is not installed."
  echo "  Debian/Ubuntu: sudo apt install git-lfs && git lfs install"
  echo "  macOS: brew install git-lfs && git lfs install"
  echo "  Windows: https://git-lfs.com"
  exit 1
fi

echo "==> Cloning fresh copy of Quantumpass"
rm -rf "$CLONE_DIR"
git clone "$REPO_URL" "$CLONE_DIR"
cd "$CLONE_DIR"

echo "==> Adding .gitattributes for MP4 files"
cat > .gitattributes <<'ATTR'
public/*.mp4 filter=lfs diff=lfs merge=lfs -text
src/assets/*.mp4 filter=lfs diff=lfs merge=lfs -text
ATTR
git add .gitattributes

if [ ! -f LICENSE ]; then
  echo "==> Adding MIT license"
  cat > LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Komnsensei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
  git add LICENSE
fi

echo "==> Migrating MP4 history to LFS (this rewrites ALL commit SHAs)"
git lfs migrate import --include="public/*.mp4,src/assets/*.mp4" --everything

git add .gitattributes LICENSE
git -c user.name="Komnsensei" -c user.email="shawnru391@gmail.com" \
  commit -m "chore: track media via Git LFS and add MIT license

History was rewritten with 'git lfs migrate' so the MP4s are stored as
LFS pointers instead of blobs. This shrinks the repo dramatically.

Generated with Codebuff 🤖
Co-Authored-By: Codebuff <noreply@codebuff.com>"

echo ""
echo "==> DONE. Push with (force, because history was rewritten):"
echo "    cd \"$CLONE_DIR\" && git push --force --all origin"
echo ""
echo "NOTE: GitHub LFS has a free quota (1 GB storage / 1 GB bandwidth)."
echo "      ~275 MB of media will consume a large chunk of it."
