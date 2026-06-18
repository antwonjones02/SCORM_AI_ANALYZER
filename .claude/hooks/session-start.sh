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
#    apt update can fail on third-party PPAs (403s) that are unrelated to us;
#    keep going regardless and suppress the noise.
if command -v apt-get >/dev/null 2>&1; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq >/dev/null 2>&1 || true
  $SUDO apt-get install -y -qq \
    ffmpeg \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 libffi-dev \
    >/dev/null 2>&1 || true
fi

# 2. Python dependencies (pip install reuses cached wheels on re-runs).
python3 -m pip install --quiet --root-user-action=ignore -r requirements.txt

# 3. Playwright Chromium for the --player browser pipeline (opt-in).
#    The browser binary download (cdn.playwright.dev) is blocked by some
#    network policies, so this is skipped unless explicitly requested.
#    Enable by setting SCORM_INSTALL_BROWSER=true in the environment.
if [ "${SCORM_INSTALL_BROWSER:-}" = "true" ]; then
  python3 -m playwright install --with-deps chromium \
    || python3 -m playwright install chromium \
    || echo "[session-start] Playwright Chromium download blocked; --player pipeline unavailable."
fi

echo "[session-start] SCORM AI Analyzer dependencies installed."
