# Validation

What has been exercised against the real services, as opposed to the
automated tests. The automated tests use the deterministic demo backend and a
scripted stand-in for the Agent SDK client.

Environment: Linux (WSL2), Python 3.12, `claude-agent-sdk` 0.2.168 with its
bundled Claude Code CLI 2.1.301, a Claude Max subscription signed in via
`claude auth login`, the GitHub CLI signed in, and Chromium (headless) for UI
checks. Runs used the `haiku` model to keep usage small.

## Claude Code through the Agent SDK

| Check | Result |
|---|---|
| Provider status | `claude auth status` is parsed: logged in, `authMethod: claude.ai`, `subscriptionType: max`. Settings shows the account and plan. |
| Billing guard | Every real session reported `apiKeySource: "none"` (subscription) and first-party usage, and none was stopped by the guard. Blanking inherited `ANTHROPIC_API_KEY`/Vertex variables, and treating a missing `apiKeySource` as a violation, are covered by unit and scripted tests; the validation machine had no API key set. |
| Boundary hook | A direct SDK probe asked an agent to read `/etc/hostname`; the `PreToolUse` hook denied it. |
| Approvals | Bash commands outside the allow-list reached `can_use_tool` and appeared as approvals (3 tool approvals; plus 2 plan approvals, one of them cancelled by the restart). Approving through the UI and API let the agents continue. |
| Single run (BOS-6) | Implement `mean()` with tests in a scratch repository: 1 session, 1 approval, tests passing (4 passed), task moved to Review, diff shown. |
| Team run (BOS-7) | Plan → approval → implementer + tester in their own child worktrees → merged into the run branch → reviewer → coordinator summary. 4 sessions, all on the subscription. The backend was restarted mid-run: the run became *interrupted* and the pending plan approval was cancelled. Resume re-requested the plan approval, then continued the same sessions. |
| Single run + integrate (BOS-9) | Add `median()` with tests. The agent's own test run passed 6/6. **Merge into main** first refused without `confirm`, then merged (`--no-ff`). The merged code passes 6/6 on the user's branch. |
| Shell sandbox | With *Run shell commands in Claude Code's sandbox* on: without `socat` installed, the CLI ran the command **unsandboxed** until `failIfUnavailable` was added; it now refuses to start, and the run fails with `sandbox_unavailable`. With `bubblewrap` and `socat` available: a write inside the worktree succeeded, and a write to `$HOME` written to evade the path heuristic (`H=/ho; touch "${H}me/…"`) failed with "Read-only file system". Commands still went through the approval queue. |
| Agent environment | Agent `python3` was Bosun's own virtualenv when launched with `uv run`. Fixed: the app's venv is removed from agent `PATH`. |

## GitHub (read-only)

| Check | Result |
|---|---|
| Auth | The token was taken from the GitHub CLI at request time. Status shows the login, the token source (`gh`) and the remaining rate limit. |
| Repository listing | 23 repositories listed. |
| Sync | `ArrushC/workstation` synced: 2 open pull requests imported into the Inbox. A second sync reported `created: 0, unchanged: 2`, so nothing was duplicated. |
| PR tasks | A task created from a PR started its worktree from the PR head (fetched read-only into `refs/bosun/pr-N`, commit `98f9038`), not from the local branch. |
| Writes | None. The client only implements `GET`. |

## MCP interface

`bosun-mcp` was started over stdio by an MCP client:

- `list_tools` returned the six tools.
- `list_workspaces`, `list_tasks` and `list_pending_approvals` returned live
  data.
- `create_task` and `start_run` (demo backend) created a task and a run that
  completed.
- `get_run_status` reported the run.

## UI

The pages below were checked in headless Chromium at 1440×900 in the dark
theme, with no console errors. The dashboard was also checked in the light
theme. After the redesign (Atkinson Hyperlegible Next, roomier layout),
every page, the task sheet, both task dialogs and the collapsed sidebar
were checked again at 1440×900 in both themes against demo-backend runs,
again with no console errors. The screenshot below predates the redesign.

- Dashboard, Board, Inbox, Agent graph, Runs
- A real team run and a real single run (graph, transcripts, changes)
- Integrations and Settings
- Task sheet and launch dialog

![Team run](images/team-run.png)

## Docker image and compose setup

Checked on Docker Desktop 29.8 (WSL2) with Docker Compose 5.5, and before
that on Podman 5.8, using the same `docker compose` CLI against Podman's
Docker-compatible API. End-to-end checks used a separate throwaway stack
(`-p`, its own port, data and test repository).

| Check | Result |
|---|---|
| Image | `docker compose build` (BuildKit) succeeded in about 90 seconds. Inside: Claude Code 2.1.301, Python 3.12, uv, Node.js 24, git, running as uid 1000. |
| `scripts/docker-setup.sh` | Wrote UID/GID and the projects, data and home folders to `.env` and kept them on a re-run. It made the home folder private (`drwx------`). It refused `/`, the home directory, a missing folder and a container home inside the projects folder. shellcheck is clean. |
| Home folder | The container's home is the host's `~/.config/bosun`, with no Docker volumes left. Claude Code's `.claude.json` and `.claude/` appeared there, owned by the host user. An MCP server added with `claude mcp add --scope user` in the container was written to that `.claude.json`, and removing it removed it there. npm's cache is in its `.cache` folder. |
| Network | Published on `127.0.0.1` only. A foreign `Host` and a cross-site `Origin` were both rejected with 403. |
| Same-path mounts | A workspace in the mounted projects folder was scanned (TODO found). A demo-backend run created its worktree in the mounted data folder. The host's own `git worktree list` showed it at a valid path, and the database, worktree and git metadata were owned by the host user. **Merge into main** produced a merge commit in the host repository. |
| Agent environment | Inside the container, agent processes saw no `BOSUN_*` variables, and `python3` resolved to the system interpreter, not Bosun's virtualenv. |
| Lifecycle | `compose stop` shut down cleanly in about a second (tini forwards SIGTERM). After a restart, data was intact and the health check reported healthy. |
| Claude login | `claude auth status` reported signed out. `claude auth login` printed a sign-in URL and waited for the pasted code. A full sign-in needs a person, so it was not completed. |
| MCP | `bosun-mcp` run with `exec -i` inside the container listed its six tools and returned live data. |
| UI | The Settings page served from the container showed the container notice under the sandbox setting, with no console errors. |
| Podman | Compose's BuildKit builder crashes under rootless Podman, so there the image has to be built with `podman build`. Everything else above also passed on Podman. |

## Automated tests

- Tests (commands in the README's Development section)
  - Backend: 108 tests. They cover:
    - the scanner, GitHub client and connector (mock transport);
    - single, team and auto runs with the demo backend;
    - worktrees and integration;
    - approvals and their races;
    - pause, cancel, resume and retry races;
    - restart recovery and failure kinds;
    - the Claude adapter against a scripted SDK client (event mapping, billing
      guard, timeouts during approvals, background tasks, sandbox fail-closed);
    - the HTTP API and WebSocket;
    - redaction and the workspace boundary.
  - Frontend: 23 tests. They cover the graph reducer over recorded event logs,
    the event-log merge, and UI workflows (create a task, launch with
    idempotency, approvals, board moves, live updates).
- Lint: ruff, basedpyright (standard) and `tsc` are clean.

## Not validated against real services

- Anthropic API key and Vertex AI providers. These are implemented and covered
  by unit tests for environment and billing-guard expectations, but no billed
  run was made.
- macOS (Seatbelt) sandboxing.
- A real Claude run inside the Docker container (it needs the container's own
  Claude sign-in).
- Very long runs (hours), and runs near the subscription usage limit. The
  `usage_limit` path is tested with scripted CLI output only.
