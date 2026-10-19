#!/usr/bin/env bash
# Prepare .env for `docker compose`: your user and group IDs, the folder of
# projects Bosun may work on, and where the container keeps its data.
# Usage: scripts/docker-setup.sh [PROJECTS_DIR]. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

[[ -f "$ENV_FILE" ]] || cp "$ROOT/.env.example" "$ENV_FILE"

current() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

set_var() {
  local key="$1" value="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" \
      'index($0, key "=") == 1 { print key "=" value; next } { print }' "$ENV_FILE" >"$tmp"
    cat "$tmp" >"$ENV_FILE" && rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

projects="${1:-$(current BOSUN_PROJECTS_DIR)}"
if [[ -z "$projects" && -d "$HOME/git" ]]; then
  projects="$HOME/git"
fi
if [[ -z "$projects" ]]; then
  echo "Which folder holds your projects? Run: scripts/docker-setup.sh /path/to/projects" >&2
  exit 1
fi
projects="${projects/#\~/$HOME}"
if [[ ! -d "$projects" ]]; then
  echo "$projects is not a directory." >&2
  exit 1
fi
projects="$(cd "$projects" && pwd -P)"
if [[ "$projects" == "/" || "$projects" == "$(cd "$HOME" && pwd -P)" ]]; then
  echo "Pick a folder of projects, not / or your home directory: everything in it is" >&2
  echo "mounted into the container where agents run." >&2
  exit 1
fi

# host_dir VAR DEFAULT: the folder already set in .env, or DEFAULT; created if missing.
host_dir() {
  local dir
  dir="$(current "$1")"
  dir="${dir:-$2}"
  dir="${dir/#\~/$HOME}"
  mkdir -p "$dir"
  (cd "$dir" && pwd -P)
}

data="$(host_dir BOSUN_DOCKER_DATA_DIR "${XDG_DATA_HOME:-$HOME/.local/share}/bosun")"
home_dir="$(host_dir BOSUN_DOCKER_HOME_DIR "${XDG_CONFIG_HOME:-$HOME/.config}/bosun")"
for other in "$projects" "$data"; do
  if [[ "$home_dir/" == "$other/"* || "$other/" == "$home_dir/"* ]]; then
    echo "Keep the container's home ($home_dir) apart from $other: it holds logins." >&2
    exit 1
  fi
done
# It holds the container's Claude login and other credentials.
chmod 700 "$home_dir"

set_var BOSUN_UID "$(id -u)"
set_var BOSUN_GID "$(id -g)"
set_var BOSUN_PROJECTS_DIR "$projects"
set_var BOSUN_DOCKER_DATA_DIR "$data"
set_var BOSUN_DOCKER_HOME_DIR "$home_dir"

cat <<EOF
Wrote $ENV_FILE:
  Projects (mounted read-write):  $projects
  Data (database and worktrees):  $data
  Home (logins and settings):     $home_dir
  Runs as user:                   $(id -u):$(id -g)

Next:
  docker compose up -d --build
  docker compose exec bosun claude auth login   # sign Claude Code in to your subscription
  open http://127.0.0.1:$(current BOSUN_PORT | grep . || echo 8787)
EOF
