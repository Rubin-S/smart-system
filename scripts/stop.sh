#!/usr/bin/env bash
set -euo pipefail
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp >/dev/null 2>&1 || true
  fuser -k 5173/tcp >/dev/null 2>&1 || true
  fuser -k 8554/tcp >/dev/null 2>&1 || true
fi
echo "stopped api, web, and rtsp listeners"
