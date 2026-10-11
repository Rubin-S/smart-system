#!/usr/bin/env bash
# Local entry point: PostGIS, a clean seed, the API, and the web app.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p .run media/evidence media/clips

free_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
    return
  fi
  local pids
  pids="$(ss -ltnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
}

free_port 8000
free_port 5173

if ! podman container exists smartpark-db >/dev/null 2>&1; then
  podman run -d --name smartpark-db \
    -e POSTGRES_USER=smartpark \
    -e POSTGRES_PASSWORD=smartpark \
    -e POSTGRES_DB=smartpark \
    -p 5432:5432 \
    docker.io/postgis/postgis:16-3.4
else
  podman start smartpark-db >/dev/null
fi

ready=0
for _ in $(seq 1 60); do
  if podman exec smartpark-db pg_isready -U smartpark >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "PostGIS did not become ready" >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

PYTHONPATH="$ROOT" .venv/bin/python -m api.seed >/dev/null

export PYTHONPATH="$ROOT"
export DEMO_GRACE_SECONDS="${DEMO_GRACE_SECONDS:-1}"
export WORKER_KEY="${WORKER_KEY:-dev-worker-key}"
nohup .venv/bin/uvicorn api.app.main:app --host 127.0.0.1 --port 8000 > .run/api.log 2>&1 &
echo $! > .run/api.pid

if [[ ! -d web/node_modules ]]; then
  npm --prefix web install
fi
nohup npm --prefix web run dev -- --host 127.0.0.1 --port 5173 > .run/web.log 2>&1 &
echo $! > .run/web.pid

for _ in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null && curl -sf http://127.0.0.1:5173/ >/dev/null; then
    echo "API     http://127.0.0.1:8000"
    echo "Citizen http://127.0.0.1:5173/"
    echo "Police  http://127.0.0.1:5173/police"
    exit 0
  fi
  sleep 0.5
done

echo "stack did not become ready" >&2
tail -n 50 .run/api.log .run/web.log >&2 || true
exit 1
