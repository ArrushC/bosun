# syntax=docker/dockerfile:1
# Bosun in one image: the API, the built web UI and the Claude Code CLI that
# agents run through. Agents run inside this container too, so it also carries
# the everyday tools they need (git, Python with uv, Node.js with npm).
# Build and run it with `docker compose` (see compose.yaml and the README).

ARG NODE_IMAGE=docker.io/library/node:24-trixie-slim
ARG PYTHON_IMAGE=docker.io/library/python:3.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node

# --- Web UI -------------------------------------------------------------------
FROM ${NODE_IMAGE} AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Runtime ------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

# Your user and group IDs, so files Bosun writes in your projects stay yours.
ARG BOSUN_UID=1000
ARG BOSUN_GID=1000
# Extra Debian packages for the projects your agents work on (e.g. "build-essential golang").
ARG EXTRA_APT_PACKAGES=""

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git git-lfs jq make procps ripgrep tini ${EXTRA_APT_PACKAGES} \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra mcp --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --extra mcp \
 && python -m compileall -q src \
 && ln -s "$(.venv/bin/python -c 'import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")')" /usr/local/bin/claude
COPY --from=ui /src/frontend/dist /app/frontend/dist

RUN (getent group "${BOSUN_GID}" >/dev/null || groupadd --gid "${BOSUN_GID}" bosun) \
 && useradd --uid "${BOSUN_UID}" --gid "${BOSUN_GID}" --create-home --home-dir /home/bosun --shell /bin/bash bosun \
 && install -d -o "${BOSUN_UID}" -g "${BOSUN_GID}" /data

ENV PATH=/app/backend/.venv/bin:$PATH \
    HOME=/home/bosun \
    BOSUN_DATA_DIR=/data \
    DISABLE_AUTOUPDATER=1
USER bosun
WORKDIR /app
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8787/api/v1/health >/dev/null || exit 1
ENTRYPOINT ["tini", "--"]
CMD ["bosun"]
