#!/usr/bin/env bash
# Start Bosun from this checkout as your everyday instance: the API and the
# built web UI together on http://127.0.0.1:8787, without hot reload.
#
# Where systemd runs (Linux, and WSL with systemd enabled), Bosun runs as a
# user service: the first run installs it, later runs start it if it is not
# running. Elsewhere, or with --foreground, Bosun runs in this terminal.
# Usage: scripts/start.sh [--foreground]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/bosun.service"

case "${1:-}" in
  "") foreground=false ;;
  --foreground) foreground=true ;;
  *)
    echo "Usage: scripts/start.sh [--foreground]" >&2
    exit 2
    ;;
esac
systemctl --user show-environment >/dev/null 2>&1 || foreground=true

run() {
  # Keep this instance's database and worktrees apart from scripts/dev.sh's
  # (.data in this folder), unless the environment or .env sets a folder.
  if [[ -z "${BOSUN_DATA_DIR:-}" ]] && ! grep -qs '^BOSUN_DATA_DIR=' "$ROOT/.env" "$ROOT/backend/.env"; then
    export BOSUN_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bosun"
  fi

  cd "$ROOT/frontend"
  if [[ ! -d node_modules || package-lock.json -nt node_modules/.package-lock.json ]]; then
    echo "Installing frontend dependencies…"
    npm install --no-audit --no-fund
  fi
  if [[ ! -f dist/index.html || -n "$(find . \( -path ./node_modules -o -path ./dist \) -prune -o -type f -newer dist/index.html -print -quit)" ]]; then
    echo "Building the web UI…"
    npm run build
  fi

  cd "$ROOT/backend"
  uv sync --quiet
  exec .venv/bin/bosun
}

unit_file() {
  # systemd gives services a bare PATH. A login shell gives Bosun, and the
  # agents it starts, your usual one: uv, Node.js, gh and your toolchains.
  local shell root="${ROOT//%/%%}" # % starts a systemd specifier
  shell="$(getent passwd "$(id -un)" | cut -d: -f7)"
  cat <<EOF
[Unit]
Description=Bosun
StartLimitIntervalSec=10min
StartLimitBurst=3

[Service]
WorkingDirectory=$root
ExecStart=${shell:-/bin/bash} -lc 'exec "\$\$0" --foreground' "$root/scripts/start.sh"
Restart=on-failure
RestartSec=10
# SIGTERM reaches Bosun alone, so it can stop its agent sessions itself.
KillMode=mixed

[Install]
WantedBy=default.target
EOF
}

if $foreground; then
  run
fi

port="${BOSUN_PORT:-$(sed -n 's/^BOSUN_PORT=//p' "$ROOT/.env" 2>/dev/null | tail -n 1)}"
url="http://127.0.0.1:${port:-8787}"

if [[ "$(unit_file)" != "$(cat "$UNIT" 2>/dev/null)" ]]; then
  echo "Installing the bosun user service…"
  mkdir -p "$(dirname "$UNIT")"
  unit_file >"$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --quiet bosun.service
  systemctl --user restart bosun.service
elif systemctl --user is-active --quiet bosun.service; then
  echo "Bosun is already running on $url. After an update: systemctl --user restart bosun"
  exit 0
else
  systemctl --user enable --quiet bosun.service
  systemctl --user start bosun.service
fi

# Without lingering, user services stop when your last session ends (on WSL,
# when the last terminal closes) and start again only at the next login.
if [[ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]]; then
  if loginctl enable-linger "$(id -un)" 2>/dev/null; then
    echo "Enabled lingering, so Bosun keeps running when you log out."
  else
    echo "Could not enable lingering; Bosun stops when your last session ends. To fix: sudo loginctl enable-linger $(id -un)" >&2
  fi
fi

echo "Starting Bosun (the first start builds the web UI)…"
for _ in $(seq 1 180); do
  if curl -fsS -o /dev/null "$url/api/v1/health" 2>/dev/null; then
    echo "Bosun is running on $url. Logs: journalctl --user-unit bosun -f"
    exit 0
  fi
  sleep 1
done
echo "Bosun did not come up within 3 minutes. Recent log:" >&2
journalctl --user-unit bosun -n 30 --no-pager -o cat >&2
exit 1
