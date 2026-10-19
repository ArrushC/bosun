#!/usr/bin/env bash
# Start the backend (FastAPI, auto-reload) and the frontend (Vite) together.
# Ctrl-C stops both. Both bind to 127.0.0.1 only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BOSUN_LAUNCH_CWD="${BOSUN_LAUNCH_CWD:-$(pwd)}"
BACKEND_PORT="${BOSUN_PORT:-8787}"

cleanup() {
  trap - INT TERM EXIT
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Installing frontend dependencies…"
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund)
fi

echo "Backend  → http://127.0.0.1:${BACKEND_PORT}  (API docs at /api/v1/docs)"
(cd "$ROOT/backend" && uv run bosun --reload) &
BACKEND_PID=$!

# Wait for the API before starting the UI so the first page load succeeds.
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/v1/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

echo "Frontend → http://127.0.0.1:5173"
(cd "$ROOT/frontend" && BOSUN_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" npx vite --host 127.0.0.1) &
FRONTEND_PID=$!

wait -n "$BACKEND_PID" "$FRONTEND_PID"
