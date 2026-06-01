#!/bin/bash
# SessionStart hook: install dependencies for the SCORM AI Analyzer so the
# AI, PDF, Excel, video, and browser pipelines work in Claude Code on the web.
set -euo pipefail

# Only run in the remote (web) environment; local setups manage their own deps.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$PROJECT_DIR"

# 1. System packages: ffmpeg (video pipeline) + WeasyPrint runtime libraries.
if command -v apt-get >/dev/null 2>&1; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq || true
  $SUDO apt-get install -y -qq \
    ffmpeg \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 libffi-dev || true
fi

# 2. Python dependencies (pip install reuses cached wheels on re-runs).
python3 -m pip install --quiet --root-user-action=ignore -r requirements.txt

# 3. Playwright Chromium for the --player browser pipeline.
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium || true

echo "[session-start] SCORM AI Analyzer dependencies installed."
