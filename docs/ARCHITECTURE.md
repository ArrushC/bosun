# Architecture

Bosun is one Python process (FastAPI on asyncio) that owns a
SQLite database, a set of git worktrees and the Claude Code processes it
starts. The React app is a client of its versioned HTTP API and a single
WebSocket.

```
 Browser (React, TanStack Query, Zustand, React Flow)
   │  HTTP /api/v1/*                     WebSocket /api/v1/ws
   ▼                                      ▲  events (seq-ordered), entity changes
 LocalRequestGuard (Host / Origin / token)│
   ▼                                      │
 FastAPI routes ──► services ─────────────┤
   tasks, sources, runs, approvals,       │
   settings, dashboard, github            │
                                          │
 IngestionService        RunManager ──► SessionRunner ──► AgentAdapter
   workspace scanner       │  TeamExecutor                   ├─ ClaudeAgentAdapter (Agent SDK → Claude Code CLI)
   GitHub (GET-only)       │  ApprovalBroker                 └─ FakeAgentAdapter (demo + tests)
   periodic sync           │  WorktreeManager (git)
                           ▼
                        EventBus ── persist (redacted) ──► SQLite (WAL) ──► broadcast
```

## Backend modules

| Package | Responsibility |
|---|---|
| `config.py`, `settings_store.py` | Process settings from `BOSUN_*` env / `.env`; user preferences (provider, execution limits, permissions, scan rules) stored in the `settings` table. |
| `db/` | SQLAlchemy 2 async models, Alembic migrations (run at startup), flush-time secret redaction for free-text columns. |
| `domain/` | Enums and the state-machine tables (below). |
| `ingestion/` | `SourceConnector` interface, workspace scanner (git-aware file walker, comment/markdown/TODO-file parsers), read-only GitHub client and connector, record upsert/missing/update handling, periodic sync. |
| `tasks/` | Task creation, numbering (`BOS-n`), activity log. |
| `agents/` | Adapter contract (`contracts.py`), Claude Agent SDK adapter, fake adapter, permission profiles and the workspace boundary, provider runtimes and the billing guard, prompts, built-in roles. |
| `orchestration/` | `RunManager` (lifecycle, controls, recovery), `SessionRunner` (one agent session), `TeamExecutor` (plan → schedule → aggregate), `ApprovalBroker`, `Limiter`, `WorktreeManager`. |
| `events/` | `EventBus`: redact → persist → publish; history paging; entity notifications. |
| `api/` | Routes, Pydantic schemas (the OpenAPI schema generates the frontend's TypeScript types), WebSocket. |
| `security/` | `LocalRequestGuard` middleware and redaction. |
| `mcp_server.py` | Optional stdio MCP server that calls the HTTP API. |

## Data model

- **Workspace**: a local folder (git or not), with an optional *trusted* flag
  that lets agents load the repository's own `.claude/` settings.
- **SourceConnection / SourceRecord**: where items come from and each
  discovered item. A record's identity is `(scope, external_id)`, kept
  separate from tasks. A task can link to several records (a TODO comment and
  the issue tracking it), and records can wait in the Inbox without becoming
  tasks. Scanner IDs are stable: path + tag + normalised text + occurrence.
  Edits to the surrounding code don't create duplicates.
- **Task** (+ dependencies, activity): the board item.
- **AgentRun**: one execution attempt for a task. It holds a frozen
  `RunConfig` snapshot (roles, tools, limits, provider), worktree and branch,
  base commit and integration status. A partial unique index allows at most
  one *active* run per task.
- **AgentSession**: one Claude Code session (coordinator, agent or follow-up),
  with its SDK session ID for resume, usage, and the auth source it reported.
- **Subtask**: a node of the team plan with dependencies, role, attempts,
  child worktree and report.
- **ExecutionEvent**: the append-only event log (global autoincrement `seq`).
- **ApprovalRequest**, **Artifact**, **SettingEntry**.

## State machines

All transitions go through `domain/state_machines.py`.

**Task.** People can move cards between columns freely. The system can only
move a task into *In progress* when a run starts, and then to *Review*
(success), *Blocked* (failure) or back to *Ready* (cancelled). **Done is never
set by the system**, and a task with an active run cannot be marked Done.

**Run.**

```
queued → preparing → planning → waiting_approval ↔ running → completed
                        │              │              │  ↘ failed
                        └──────────────┴──→ paused ←──┘  ↘ cancelled
                                                          ↘ interrupted (restart)
completed / failed / interrupted / paused ──(resume, retry, follow-up)──► queued
```

**Subtask.** `pending → waiting_dependency → running ⇄ waiting_approval →
completed | failed | conflict`, plus `skipped` and `cancelled`. Failed,
conflicting, skipped and cancelled subtasks can be sent back to `pending` by
the operator (retry). On resume, interrupted subtasks continue their own
Claude session.

## Running agents

### Sessions

`SessionRunner` turns a `SessionRequest` (role, prompt, cwd, worktree) into a
`SessionSpec`, waits for a slot in the global `Limiter`, and hands it to the
selected adapter. A session is registered with its run before it waits for a
slot, so pausing or cancelling a run also stops sessions that are still
queued. The adapter reports everything through `SessionIO`:

- `emit` sends observable events.
- `request_permission` asks for an approval.
- `inbox` holds operator messages.
- `stop_event` signals a pause or cancel.
- `set_pid` reports the Claude Code process ID.

`ClaudeAgentAdapter` drives `ClaudeSDKClient`:

- **Options:**
  - `tools` sets the built-in tools that exist.
  - `allowed_tools` sets the rules that run without asking.
  - `disallowed_tools` removes tools outright.
  - Everything else reaches `can_use_tool`, and from there the approval
    queue.
  - A `PreToolUse` hook enforces the workspace boundary.
  - `setting_sources=[]` and `strict_mcp_config` are set unless the workspace
    is trusted.
  - `output_format` asks for structured output for plans.
  - Optional `sandbox` turns on the OS sandbox (with `failIfUnavailable`).
- **Message mapping:**
  - Assistant text, tool calls, results and denials, subagent spawns
    (`parent_tool_use_id`) and background task lifecycle are mapped to
    events.
  - Rate-limit events are de-duplicated.
  - Results carry usage and cost; cost counts only for API-billed providers.
- **Turn loop:**
  - A single consumer reads messages, operator messages and stop signals from
    one queue.
  - Operator messages are delivered after the current turn.
  - The session stays alive while background tasks run, and waits briefly for
    the turn Claude Code injects when they finish.
  - Time spent waiting for a human approval does not count against the
    session time limit.
  - If the agent has finished but a background task never reports back, the
    session keeps the agent's result when it hits the time limit.
  - Messages that never reached Claude go back to the session's inbox. When
    the run ends they become a follow-up turn. If the run was stopped, they
    are shown as not delivered.
- **Errors:** errors are classified as `auth_required`, `usage_limit`,
  `rate_limited`, `billing`, `billing_guard`, `cli_missing`,
  `sandbox_unavailable`, `timeout`, `max_turns`, `process_crash` and so on.
  Kinds a retry cannot fix are not retried automatically.

### Providers and the billing guard

`build_provider_runtime` produces the environment and the expectations for
the provider you selected:

- **Subscription** (default): blanks inherited `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN` and the `CLAUDE_CODE_USE_*` variables. The Agent SDK
  merges `env` over the parent environment, so variables can only be
  overridden, not removed. It expects `apiKeySource == "none"` and
  first-party usage.
- **API key** and **Vertex** are used only when chosen in Settings. Each
  passes its own credentials and expects its own key source and usage
  provider.

The adapter checks the init frame before any work and the usage providers on
every result. A missing `apiKeySource` also counts as a mismatch. Any mismatch
stops the session with `billing_guard`. Agent processes also lose Bosun's
own virtualenv from `PATH`, so they use your project's Python, not
the app's.

### Isolation

`WorktreeManager.prepare_run` creates `bosun/<task>-<slug>-<id>` from the current
HEAD. A pull-request task starts from the PR head instead, fetched read-only
into `refs/bosun/pr-N`. The worktree lives under `worktrees/<run_id>` in the
data folder. Non-git folders run in place, one writer at a time.

Team subtasks that modify code get a child worktree branched from the run
branch. When a child finishes, its changes are committed (caches excluded) and
merged `--no-ff` into the run branch under a lock. A conflict aborts the merge
and becomes an *integration approval*:

- approve: re-run the subtask on the latest run branch;
- deny: skip it;
- run stopped: re-run it on resume;
- approval timed out: leave it as a conflict for the operator.

Integrating a run into your checkout is a separate, confirmed API call
(`POST /runs/{id}/integrate` with `confirm: true`). It commits anything the
agents left uncommitted on the run branch, then refuses when:

- the run started on a detached HEAD;
- your checkout is on a different branch;
- your checkout has uncommitted changes;
- the run's base commit is not on your branch (so a PR-head run never drags
  the PR in);
- the agent branch has no new commits.

### Teams (application-managed orchestration)

1. **Plan.** A read-only Coordinator session explores the repository and
   returns a JSON plan through structured output. The plan has subtasks with
   roles, dependencies and whether each one modifies code.
   `normalise_plan` validates it:
   - roles map to configured roles;
   - keys are unique;
   - dependencies are known and acyclic (Kahn's algorithm);
   - the plan stays within the team-size limit.
   A plan the coordinator returns as text is parsed as a fallback.
2. **Approve.** By default the plan waits for your approval
   (`waiting_approval`). Rejecting it ends the run without doing work.
3. **Schedule.** Ready subtasks start as their dependencies complete, up to
   `max_parallel` per run and the global session limit. Each subtask's prompt
   includes the reports of its dependencies; Bosun also emits those
   hand-offs as `agent.message` events, which become message edges in the
   graph. Failed subtasks retry up to `subtask_max_attempts` for retryable
   errors. A subtask whose runner crashes is marked failed rather than
   restarted in a loop.
4. **Aggregate.** The coordinator session is resumed with all reports and
   writes the run summary.

**Why not just SDK subagents?** Claude Code can delegate to subagents through
the `Agent` tool, and Bosun supports that per run ("Allow Claude
subagents"). Delegation shows up in the graph from `parent_tool_use_id`. But
SDK subagents:

- share one session and working directory;
- cannot be paused, retried or given separate approvals;
- have no dependency graph Bosun can enforce.

Application-managed teams give each role its own session, worktree,
permissions, retry policy and transcript. Every hand-off is a real, recorded
event, not an invented conversation.

### Approvals

`ApprovalBroker` persists each request, marks the session and run as waiting,
emits `approval.requested`, and waits for a decision, a stop, or the timeout.
Resolution is an atomic `UPDATE … WHERE status = 'pending'`, so a decision
that races an expiry or a second click resolves exactly once. "Always allow in
this run" stores a narrow rule: the tool name, plus the command prefix for
Bash. Later matches are auto-approved and recorded as `approval.auto`. On
restart, pending approvals whose agent is gone are marked expired.

### Run controls and concurrency

- **Start.** A per-task lock plus an `Idempotency-Key` stop a double click or
  a retried request from starting a second run. The partial unique index is
  the last line of defence.
- **Resume, retry, follow-up, subtask changes, integrate.** These are
  serialised per run by a synchronous claim, so two concurrent requests never
  start two drivers.
- **Pause** interrupts sessions; they end `interrupted` and keep their SDK
  session ID for resume.
- **Cancel** always wins over a pending pause. It discards queued follow-ups
  and returns the task to Ready.
- **Interrupt with a message** (single agent): pauses, then continues the same
  session with your message. It is refused while team subtasks are running.

### Recovery

On a clean shutdown, active runs are stopped and marked `interrupted` ("Bosun
shut down while this run was active"). After a crash, startup recovery
does the same for every run still recorded as active ("Bosun
restarted while this run was active. Resume it to continue."). In both cases
sessions are marked interrupted, and pending approvals are cancelled or
expired. A recorded Claude Code process ID is
terminated only when that process is still a `claude` process tagged with
Bosun's client environment variable. Nothing is assumed to still be
running. **Resume** re-creates the run context, continues interrupted sessions
with the SDK's `resume`, and re-asks any plan approval that was pending.

A subtask whose agent had already finished when Bosun stopped (before its
changes were committed, between the commit and the merge into the run branch,
or after the merge) is not run again. Resume records it from the finished
session: anything left uncommitted in its worktree is committed, and its branch
is merged unless the run branch already contains it (`subtask.recovered`).
Retries started by the operator reset the attempt count and always start a
fresh session.

What recovery does not cover:

- An agent interrupted mid-turn is continued, so the tool call it was making may
  run again. Bosun cannot make an agent's shell commands idempotent.
- A single-agent run whose session had finished is continued with "continue
  from where you left off", which costs a turn.
- If Bosun stops after merging a run into your branch but before recording it,
  the run still reads "pending", and **Merge** then reports that the branch has
  no new commits. Check `git log` and use **Discard** to close it.
- Creating a run and moving its task to In progress are two commits; a crash
  between them leaves the task where it was and the run interrupted.
- Nothing resumes on its own; resuming is always the operator's action.

## Events, live updates and the graph

Every observable fact is an `ExecutionEvent`:

- `run.*`, `workspace.prepared`
- `session.created | init | resumed | turn_completed | injected_turn |
  notice | completed | failed | interrupted | cancelled`
- `assistant.text`, `tool.call | result | denied`, `subagent.spawned`,
  `task.*`
- `plan.created`, `subtask.*`, `agent.message`
- `approval.requested | resolved | auto`
- `artifact.file | commit`, `rate_limit`, `user.message`

Each event has `run_id`, `session_id`, `agent_id` and `parent_agent_id`.

**Persist, then broadcast.** `EventBus.emit` redacts the payload, inserts the
row, and publishes the saved event under a lock, so subscribers see events in
`seq` order. Each subscriber has a bounded queue. One that overflows gets
`resync` and refetches, rather than silently missing events.

**WebSocket protocol** (`/api/v1/ws`):

| Direction | Message | Meaning |
|---|---|---|
| server → client | `hello` (`last_seq`) | Sent on connect |
| server → client | `event` | A persisted event, in `seq` order |
| server → client | `entity` | A task, run, approval or source changed; the client invalidates the matching TanStack Query caches |
| server → client | `replay_done` | A replay request has caught up |
| server → client | `resync` | The client fell behind and must refetch |
| client → server | `subscribe` (topics) | Choose what to receive |
| client → server | `replay` (`run_id`, `after_seq`) | Catch up after reconnecting |
| both | `ping` / `pong` | Keep-alive |

The client reconnects with backoff, a 45-second staleness watchdog, and an
immediate reconnect when the network comes back or the tab becomes visible.
Logs are merged by `seq`, so replayed and live events never duplicate.

**Graph.** The browser builds the graph from the event log alone, with an
incremental reducer (`lib/graph/reduce.ts`). The nodes are:

- the task;
- one node per session (coordinator, agents) and per SDK subagent;
- plan subtasks;
- tool calls (grouped per agent, expandable);
- approvals, files and the result.

The edges are:

- spawned-by;
- plan dependencies;
- routed messages (assignments and reports);
- tool and file links.

The node state comes from the latest event for each entity. Dagre lays the
graph out left to right or top down. The flight recorder shows each agent's
lane, spans for tool calls and approvals, and marks for text and messages. It
can replay the log, rebuilding the graph from a prefix. The graph tests run the
reducer over event logs recorded from real backend runs
(`frontend/src/test/fixtures`).

## Security model

- **Local only by default.** The server binds `127.0.0.1`. `LocalRequestGuard`
  rejects:
  - a `Host` that isn't local (DNS rebinding);
  - a mutating request or WebSocket from an `Origin` not on the allow-list
    (CSRF from other sites);
  - a mutation with a non-JSON body.

  Remote binding requires both `BOSUN_ALLOW_REMOTE` and `BOSUN_AUTH_TOKEN` (bearer
  token, constant-time comparison). The web UI cannot send a token yet.
  In a container, `BOSUN_PUBLISHED_ON_LOOPBACK` lets Bosun listen on the
  container's interfaces without a token, because `compose.yaml` publishes the
  port on the host's loopback only. Bosun ignores that promise outside a
  container (`/.dockerenv` or `/run/.containerenv`) and refuses to start.
- **Boundary.** File tools are confined to the session's worktree by a hook
  that runs before every tool call, including auto-approved ones. Bash
  commands get a heuristic check. It blocks:
  - paths in `$HOME`;
  - the user's own checkout;
  - Bosun's data directory;
  - `..` escapes (`../`, `./../`, `$(pwd)/..`);
  - `cd` with no target or to `~`/`-`;
  - absolute Glob patterns.

  **It is defence in depth, not a sandbox.** Shell indirection can defeat it.
  The optional Claude Code sandbox (bubblewrap/Seatbelt) adds an
  operating-system boundary for writes and network. With it enabled, sessions
  fail closed when the sandbox cannot start.
- **Least privilege by role.**

  | Role | Shell access |
  |---|---|
  | Coordinator, Researcher | None (read-only) |
  | Reviewer | Only `git diff/log/show/status` |
  | Implementer, Tester | A small allow-list (status/diff/ls, configured test commands); everything else is an approval |

  Web tools are off unless enabled.
- **Untrusted content.** Imported text is wrapped in `<untrusted_task_data>`
  blocks with an explicit instruction that it is data. Repository Claude
  settings, hooks and MCP servers are not loaded unless the workspace is
  trusted.
- **Secrets.**
  - Redaction runs on every event payload, on approvals, and (at flush time)
    on session prompts, results, errors, run summaries, subtask reports and
    activity text.
  - It catches known token formats, bearer and basic auth, URL credentials,
    `--password`/`--token` flags, and secret-named keys including nested
    values.
  - GitHub tokens are read from the environment or `gh` when needed and never
    stored.
  - Agent processes get every `BOSUN_*` variable blanked, so settings passed
    to a container (tokens, keys) are not in their environment. They run as
    the same user as Bosun, though, so this keeps secrets out of casual reach
    (`env`, test output), not out of reach of a command that reads Bosun's
    files or process. Keep tokens read-only.
- **No external writes.** The GitHub client only issues `GET`. Nothing is
  pushed. Merging into your branch is explicit and confirmed.

### Running as a service

`scripts/start.sh` writes a systemd user unit, unless an identical one is
installed, and starts it. The unit runs `scripts/start.sh --foreground` from
the checkout through the user's login shell, so Bosun and its agents get the
user's `PATH` rather than systemd's bare one. In the foreground the script
rebuilds the web UI when its sources are newer than the last build, syncs the
backend's environment, then `exec`s the server. Systemd's `SIGTERM`
therefore reaches Bosun itself (`KillMode=mixed`), as tini forwards it in the
container. There is no container boundary: agents run as the user, with the
user's Claude Code and `gh` logins. The boundary above, approvals and the
optional sandbox keep them to their worktree.

### Running in a container

The image holds the backend, the built UI, the bundled Claude Code CLI and
the tools agents need (git, Python with uv, Node.js with npm). Agents run in
the same container, so the container is their boundary: they can reach the
mounted projects folder, the data folder, the container user's home (holding
the container's own Claude login, settings and MCP tokens) and the network.

Projects and data are bind-mounted at the same path inside and out. The
database stores host paths, and the `.git/worktrees/*/gitdir` links that
`git worktree add` writes into your repositories point at the data folder.
With matching paths, `git worktree list` (and `git worktree prune`) on the
host see valid worktrees instead of pruning them. The home folder is also a
bind mount, at `/home/bosun`, so all state (logins included) lives in host
folders and none in Docker volumes. The image is built with your
user and group IDs so those files stay yours. tini runs as PID 1 and
forwards `SIGTERM`, so a stop marks active runs interrupted as usual.

### Trade-offs and limitations

- **Shell paths.** The Bash path check is heuristic. Use the sandbox setting
  when you need a hard boundary.
- **Billing.** Subscription mode depends on Claude Code reporting
  `apiKeySource`. That field is treated as required, so a CLI that stops
  sending it fails closed.
- **Scale.** SQLite and a single process suit one person on one machine. The
  event log grows with use. There is no retention policy yet.
- **Team hand-offs.** Team agents communicate only through reports that
  Bosun routes between steps. There is no live agent-to-agent chat,
  by design.
- **Merges.** Merge conflicts in team runs are resolved by re-running a
  subtask, not by automated conflict resolution.
