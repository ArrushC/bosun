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
theme.

- Dashboard, Board, Inbox, Agent graph, Runs
- A real team run and a real single run (graph, transcripts, changes)
- Integrations and Settings
- Task sheet and launch dialog

![Team run](images/team-run.png)

## Automated tests

- `make test`
  - Backend: 106 tests. They cover:
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
- `make lint`: ruff, basedpyright (standard) and `tsc` are clean.

## Not validated against real services

- Anthropic API key and Vertex AI providers. These are implemented and covered
  by unit tests for environment and billing-guard expectations, but no billed
  run was made.
- macOS (Seatbelt) sandboxing.
- Very long runs (hours), and runs near the subscription usage limit. The
  `usage_limit` path is tested with scripted CLI output only.
