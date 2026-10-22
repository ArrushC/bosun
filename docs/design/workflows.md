# Workflows: authored agent graphs

Status: proposal, revision 2 (2026-10-20), against `main` at `e29f98f`.
Milestone 1 is done and deployed. Milestone 2 is implemented on the
`workflows-m2` branch and awaits review ([status](#milestone-2-status-2026-10-22-implemented-awaiting-review)).
Milestones 3 and 4 are proposals. A spike tested the central assumption; its code
is on the local branch `spike/workflow-engine` (worktree `../bosun-spike`),
in three reviewable commits:

| Commit | What it is |
|---|---|
| `d05dab8` | Backend tests that run fixed workflows on the unchanged team engine, a recorder for UI inspection, and graph fixtures |
| `578b762` | Prototype of the live-graph changes those runs need |
| `93d0a48` | Static mockups of the proposed screens, built from Bosun's own components (development route only) |

**Labels.** *Verified* means checked in code, by a test or against current
documentation, with the evidence named. *Assumption* means not checked.
*Decision* marks a choice that is yours to make; the ones that affect
security, cost or behaviour are also listed in [section 9](#9-risks-and-decisions).

What changed since revision 1:
- The first milestone is now a vertical slice that needs **no** new run
  model: a draft workflow runs on a board task through the team engine and is
  inspected in the existing run page.
- A spike tested engine reuse. The approach holds, with specific gaps that
  are listed with their fixes.
- The Claude subscription is **no longer** the default for unattended runs,
  after checking Anthropic's current documentation.
- Unattended safeguards come before scheduling, as their own milestone.
- Rebuilding team mode as a workflow is deferred until workflows have proved
  themselves.
- The UI section is rebuilt around Bosun's existing components, with
  mockups.

---

## 1. Recommendation

1. **Run workflows on the team engine as a fixed plan.** The spike ran
   authored workflows through `TeamExecutor` by replacing only its planning
   step. Ordering, parallel steps, joins, hand-offs, child worktrees, merges,
   retries, pause and resume, transcripts, the diff and merge-to-branch all
   worked unchanged. *Verified* (7 backend tests, 116 of 116 backend tests
   pass with the spike in place).
2. **Emitting the same events is necessary but not sufficient.** The live
   graph, the run header and the failure text assume a coordinator. Those
   gaps are listed in section 2 with their fixes, three of them prototyped.
   *Verified*.
3. **Milestone 1 keeps every run on a board task.** That avoids the run-model
   refactor entirely until the unattended milestones need it.
4. **Unattended runs default to an API key or Vertex**, and need safeguards
   (credentials, tool policy, limits, safe restart) before any scheduler
   exists.

---

## 2. Spike: can a fixed workflow use the existing engine?

### Method

`backend/tests/spike_workflows/fixed_plan.py` subclasses `TeamExecutor` and
overrides only `plan()`. Instead of running a coordinator session, it writes
the workflow's steps as `Subtask` rows and emits `plan.created` with the same
payload a coordinator plan produces, plus `"source": "workflow"`.
`use_fixed_plans()` swaps the class into `RunManager` for the duration of a
test. No production file changed.

The tests use the demo adapter (real orchestration, git, database and HTTP
stack; simulated Claude sessions) with a recording wrapper that captures
prompts and can fail a step once. `record.py` recorded three runs (a
workflow, a failing workflow and an ordinary team run for comparison) into a
data folder that a Bosun instance then served, so the screenshots below are
the real run page.

The workflow under test: Research → Implement → (Add tests ∥ Review) → Final
check. That is a sequence, a parallel branch, and a join.

### Results

| Question | Result | Evidence |
|---|---|---|
| Does the engine respect authored order, run independent steps in parallel, and wait for all inputs at a join? | Yes. Research finished before Implement started; Test and Review both started before either finished; Final check started after both. | `test_fixed_plan_runs_in_order_with_parallel_steps_and_a_join` |
| Does it finish without a coordinator? | Yes. 5 sessions, no planning or summary session, run status went `queued → preparing → running → completed` (no `planning`). The summary falls back to the steps' reports joined (`_fallback_summary`). | same test |
| Are hand-offs carried? | Yes. Implement's prompt contains Research's report under "Reports from your teammates". | `test_steps_receive_earlier_reports_but_coordinator_wording` |
| Do code-changing steps get child worktrees and merge? | Yes. `artifact.commit` events for Implement and Add tests; their changes are on the run branch. | first test |
| Do the endpoints the run page uses work (run, events, transcripts, diff, merge)? | Yes, through the real HTTP stack: every session has a transcript, the diff lists the changed files, merge succeeds and the task moves to Review. | `test_ui_endpoints_serve_a_workflow_run` |
| Are retryable failures retried? | Yes, `process_crash` retried once and the run completed. | `test_retryable_failure_retries_the_step` |
| What happens after a final failure? | The failed step's dependents never start and stay `waiting_dependency`; **independent branches run to completion**; the run then fails with "Subtasks did not complete: left (failed), left2 (waiting_dependency)" and the task moves to Blocked. | `test_failed_step_blocks_dependents_but_independent_branches_finish` |
| Do pause and resume work? | Yes. Pausing mid-step and resuming continued that step's Claude session (`session.resumed`) and did not repeat finished steps. A step paused before its session started simply starts again. | `test_pause_and_resume_continue_only_unfinished_steps` (15/15 repeat runs pass) |
| What if the process dies after a step's changes merged but before the step was recorded as done? | **The step runs again from scratch on top of its own merged work**, and its edit is applied twice. | `test_crash_after_merge_reruns_the_step_on_top_of_its_own_work` |
| Does the live graph draw the run? | Partly. Steps, agents, dependencies, report messages and the outcome appear; four gaps below. | `frontend/src/test/spike/workflowGraph.test.ts`, screenshots |

The crash finding applies to **today's team runs** too: it lives in the
shared engine's resume path, not in anything workflow-specific.

### Differences in execution semantics

| Area | Team engine today | What a workflow needs | Change |
|---|---|---|---|
| Plan source | Coordinator session, then `normalise_plan` | The authored spec | A fixed-plan path in `TeamExecutor.plan()` (the spike's override, about 50 lines) |
| Unknown or disabled role | `normalise_plan` maps it to a similar role with a warning; `_run_subtask` falls back to the first role | An error before the run starts | Validate the spec at launch; no fallback for workflow steps |
| `modifies_code` with a read-only role | Silently demoted to read-only, with a warning | An error in the editor | Validation rule |
| Plan size | Truncated to `max_team_size` | The author's graph, within a hard limit | Validation rule (limit 20 steps) |
| Failure | Dependents wait forever; other branches finish; run fails | Same behaviour (keep it), but say so clearly | Mark steps that can no longer run as `skipped` with a reason when the run ends; plain-language failure text |
| Prompt wording | "Your assignment from the coordinator", "Reports from your teammates" | "Your step in workflow *X*", "Reports from earlier steps" | A workflow variant of `subtask_prompt` |
| Single step | Treated as `strategy: "single"`; the report becomes the summary | Fine as is | None |
| Mode label | `mode: "team"` | Its own mode, so the UI can say "Workflow" | `ExecutionMode.WORKFLOW` |
| Resume after a merge crash | Re-runs the step (above) | Reconcile: a step whose child branch is already merged is complete | Fix in the shared engine (benefits team runs too) |

### Differences in UI expectations

| Gap | Seen | Fix | State |
|---|---|---|---|
| The task card and the first step are not connected; the plan floats | ![](workflows/spike-graph-before.png) | First steps hang off the task when there is no coordinator | Prototyped, `578b762` ![](workflows/spike-graph-after.png) |
| The outcome hangs off whichever agent started last, which in a failed run is on the branch that succeeded | ![](workflows/spike-failed-before.png) | The outcome follows each branch's last step | Prototyped ![](workflows/spike-failed-after.png) |
| Hiding the plan leaves every agent unconnected (they re-attach to a coordinator) | test | Re-attach to the task | Prototyped |
| A step that never ran reads "Waiting on dependency" after the run failed | test, screenshot | Backend marks it `skipped` with a reason | Not prototyped (backend change) |
| Header says "Agent team"; summary panel says "Mode team, Plan approval Off"; failure text uses internal keys and status names (failed run above) | ![](workflows/spike-summary.png) | Workflow-aware labels and plain-language failure text | Not prototyped |
| Transcripts, Changes, merge and discard | ![](workflows/spike-transcripts.png) ![](workflows/spike-changes.png) | None needed | Works |

The prototype changes only take effect when a plan has no coordinator: all 24
existing frontend tests, including the recorded team-run graph tests, still
pass (34 of 34 with the spike tests).

### Verdict

Reuse holds. Milestone 1 needs the fixed-plan path, strict validation, the
workflow prompt variant, skip marking, the labels, and the three graph fixes.
The merge-crash fix is an existing engine bug that becomes more likely with
multi-step code changes. **Recommended: fix it in Milestone 1**, with the
spike's test turned into a regression test (decision D7).

To rerun: in `../bosun-spike`, `uv run --directory backend pytest
tests/spike_workflows` and `npx vitest run src/test/spike` in `frontend`.
`uv run --directory backend python -m tests.spike_workflows.record DATA FIXTURES`
records new runs.

---

## 3. Existing architecture this builds on

Unchanged from revision 1, all *verified*:
- **Team engine:** `TeamExecutor.schedule` (`orchestration/team.py:412`)
  schedules a DAG of `Subtask` rows with `max_parallel` and the global
  session `Limiter`.
- **Validation and prompts:** `normalise_plan` (`team.py:85`) validates
  plans, and `subtask_prompt` (`agents/prompts.py:175`) carries dependency
  reports.
- **Run state and recovery:** `RunConfig` (`orchestration/config.py`) is
  frozen per run, and `RunManager` (`orchestration/manager.py`) owns start,
  pause, cancel, resume, retry, integrate and recovery.
- **Approvals:** `ApprovalBroker` (`orchestration/runtime.py:353`) handles
  them, with timeout meaning deny.
- **Live graph:** it is built from events by `lib/graph/reduce.ts` and drawn
  by `components/graph/AgentGraph.tsx` with React Flow 12 and dagre.

---

## 4. Objects, by milestone

| Object | Milestone | What it is |
|---|---|---|
| Agent | exists | `AgentRole`: prompt, permission profile, model, limits |
| Workflow | 1 | A named graph of agent steps with one editable **draft** |
| Run (on a task) | 1 | An ordinary `AgentRun` with `mode: workflow` and a frozen copy of the draft it ran |
| Version | 2 | An immutable, numbered copy of the draft, with the agents' definitions frozen in |
| Deployment | 3 | A pinned version plus workspace, base branch, provider and safeguards; runs unattended on **Run now** |
| Schedule | 4 | When a deployment runs; each fire is recorded once |

---

## 5. Milestones

Each milestone ends with tests passing, the docs updated, and a **visual
review** of every new or changed screen against the existing app (procedure
in section 8).

### Milestone 1: draft, validate, run on a task, inspect

The vertical slice. A person creates a workflow, edits its draft, sees
validation as they go, runs it on a new or existing board task, and follows
and reviews the run in the run page that exists today.

**In scope**
- Agent steps and the edges between them. Several outgoing edges run in
  parallel and several incoming edges wait for all of them. No Start, End,
  approval or condition steps; the run starts with the steps that depend on
  nothing and ends when no step can run.
- A Workflows list and a blank workflow. (The Fix with review template moved
  to milestone 2.)
- The editor: canvas, add, connect, select, configure, delete, undo and redo,
  tidy layout, autosave with conflict detection, and inline validation.
- A Run dialog: a new task (title, description, workspace) or an existing
  one. The run stores a frozen copy of the draft and its revision.
- Run page: a workflow-aware header, summary and failure text; the graph
  fixes above.
- Engine: the fixed-plan path, strict validation at launch, the prompt
  variant, skip marking, `ExecutionMode.WORKFLOW`, and the merge-crash fix
  (D7).

**Out of scope:** versions, templates, approval steps, launching a
workflow from the task's existing Run dialog, runs without a task,
deployments, schedules, inputs other than the task.

**Data and API (all additive)**
- Table `workflows`: id, name, description, `spec` (JSON), `revision` int,
  archived, timestamps.
- `RunRequest.workflow_id`. `RunConfig.workflow` (optional; old runs load
  unchanged) holds `{id, name, revision, spec}`.
- `GET/POST /workflows`, `GET/PATCH/DELETE /workflows/{id}` (`PATCH` must
  carry the current `revision`, else 409). Every create and save returns
  the validation issues, so no separate validate call was needed.
  `POST /workflows/{id}/runs` (`task_id`, or `new_task: {title,
  description, workspace_id}`; `Idempotency-Key`). It creates the task if
  asked, then calls the existing `RunManager.start`.

**Acceptance criteria**
1. From an empty Workflows page, create a blank workflow, add steps with
   their agents and instructions, add a parallel step, and run it on a new
   task. The run completes in the demo backend; Changes shows the merged
   result; Merge into main works.
2. A validation error (no agent, a cycle, a code-changing read-only step)
   appears on the step and in the problems panel within one second of the
   edit. Run is disabled, with the reason, until it is fixed. The server
   refuses to start an invalid workflow with the same messages.
3. Two tabs editing the same workflow: the second save gets a conflict
   message and loses nothing silently.
4. Editing the draft while a run is going changes neither that run nor its
   page.
5. A failed step: the run page names the failed step and the steps that did
   not run, in plain words; those steps show as skipped, not waiting.
6. The graph shows task → first steps → … → outcome connected; hiding the
   plan keeps agents attached.
7. Killing the backend right after a step's merge and resuming the run does
   **not** run that step again (the spike test, inverted).
8. Every existing test passes unchanged plus the new ones below; the recorded
   team-run fixtures produce the same graph.
9. Visual review passed (section 8), including a keyboard-only walk through
   create → validate → run.
10. One real Claude run of a three-step workflow on the subscription, started
    by a person, recorded in `docs/VALIDATION.md`.

**Tests**
- **Backend:**
  - validator rules (one test per rule);
  - spec-to-plan conversion;
  - the spike tests promoted into the suite and inverted where the
    behaviour changes;
  - API tests: create, update, conflict, delete, run start with a new
    task, run start with an existing task, a repeated idempotency key.
- **Frontend:**
  - an editor store test: add, connect, delete, undo, redo, and a revision
    conflict;
  - showing server validation results on steps;
  - the reducer tests from the spike;
  - a run header test for workflow runs.
- **Regression:** listed in section 6.

### Milestone 1 status (2026-10-21): done

All ten acceptance criteria pass; the evidence is in
[VALIDATION.md](../VALIDATION.md#workflows-milestone-1), with two real Claude
runs (one completed, one failing on a step's turn limit so the skipped steps
show), browser checks for criteria 1–4 and 9, and axe-core in both themes.
Tests: 135 backend and 48 frontend, all passing; the team fixtures render as
before.

What changed from the plan:
- **Templates moved to milestone 2.** The empty Workflows page offers **New
  workflow** only.
- **No `/validate` endpoint.** Saves return the issues (see above).
- **Keyboard model:** React Flow's. `Tab` reaches steps and connections,
  `Enter` selects, the arrow keys move the selected step, `Delete` removes
  it, and `Escape` clears the selection. `A` opens the add menu, and focus
  then goes to the new step's name. **Wait for another step** in the step's
  settings connects existing steps without a mouse. Arrow keys don't jump
  between connected steps, as section 8 suggested.
- **Not built:** dragging from a step onto empty canvas to create one.
  Loops are refused with a toast rather than a tooltip. The conflict
  choices are **Load theirs** and **Keep mine**.
- **Found and fixed on the way** (beyond workflows):
  - the shared dropdown's background;
  - focus returning to whatever opened a dialog;
  - light-theme status colours and muted text, which were below WCAG AA;
  - the run-header link, which was told apart by colour alone.
- **Still open:** while any menu is open, axe reports `aria-hidden-focus`.
  It comes from Radix's modal menus and affects every menu in the app. It is
  tracked, with steps to reproduce, in
  [VALIDATION.md](../VALIDATION.md#open-follow-ups). axe-core and Playwright
  ran from a scratch folder, not as project dependencies.

Implemented screens:

| Screen | Image |
|---|---|
| Workflows list | ![](workflows/m1-list.png) |
| A new, empty workflow | ![](workflows/m1-empty.png) |
| Editor, step selected | ![](workflows/m1-editor.png) |
| Editor, light theme | ![](workflows/m1-editor-light.png) |
| Problems and warnings | ![](workflows/m1-problems.png) |
| Add a step | ![](workflows/m1-add-menu.png) |
| Deleted step with Undo | ![](workflows/m1-undo.png) |
| Save conflict | ![](workflows/m1-conflict.png) |
| Keyboard focus on a step | ![](workflows/m1-keyboard-focus.png) |
| 1024×768, settings over the canvas | ![](workflows/m1-narrow.png) |
| Run dialog | ![](workflows/m1-run-dialog.png) |
| Run refused | ![](workflows/m1-run-refused.png) |
| A real run, graph | ![](workflows/m1-run-graph.png) |
| A failed run with skipped steps | ![](workflows/m1-run-failed.png) |

### Milestone 2: versions, templates, launching from a task

- Publish a draft as an immutable version (agents frozen in). View old
  versions, restore one to the draft, and see what changed.
- Templates, starting with Fix with review (moved from milestone 1).
- A **Workflow** option in the task's Run dialog.
- An **approval step**: a human gate, using `ApprovalBroker` with a new
  approval kind, re-asked on resume like plan approvals.
- A **Steps** tab on the run page: the authored layout with live status
  (mockup below).

Acceptance follows milestone 1's pattern: functional tests, a visual review,
and one real run.

### Milestone 2 status (2026-10-22): implemented, awaiting review

Built on the `workflows-m2` branch. It is not merged, pushed or deployed. The
evidence is in [VALIDATION.md](../VALIDATION.md#workflows-milestone-2): one
real Claude run through an approval step, browser checks of every flow
including a crash while an approval waited, a keyboard-only walk-through,
axe-core on thirteen screens in both themes at two sizes, and the migration
tested on copies of milestone 1's data. Tests: 161 backend and 70 frontend,
all passing.

**What it does**

- **Versions.** **Publish** freezes the draft and the definitions of the
  agents its steps use into a numbered, immutable version (the database
  refuses changes to it). The editor says whether the draft is not published,
  the same as version N, or changed since. The **Versions** tab lists the
  draft and every version; a version's page shows its steps and agents, what
  changed from the version before, and offers **Run** and **Restore to
  draft**. Every run records the version it ran, or the draft and its
  revision, in its header, summary and the workflow's run list.
- **Templates.** Fix with review, Research report and Implement and test
  (the three from the mockup), offered on the empty Workflows page and in the
  **New workflow** dialog. A template's preview lists its steps and what to
  set up before running (its agents turned on, a workspace, and, recommended,
  test commands). Creating from one copies its steps into an independent
  draft.
- **Launching from a task.** The task's Run dialog has a **Workflow**
  strategy with a workflow and a version.
- **Approval steps.** A step that waits for you, added from any **Add a step**
  menu. Behaviour is below.
- **Steps tab.** Workflow runs open on it: the steps as the run froze them,
  in their layout or as a list in execution order, each with its status,
  attempts and the reason for it, and a panel with the step's agent,
  instructions, the prompt it was given, its report, every attempt's
  transcript, and an approval step's request and decision.

**Decisions taken with you (2026-10-22)**

| ID | Decision | Taken |
|---|---|---|
| D10 | Launch a workflow from the task's Run dialog in this milestone | Yes |
| D11 | Can the draft still run once versions exist? | Yes, labelled Draft everywhere. The Run dialog defaults to the latest version. |
| D12 | What a version runs with when an agent changes after publishing | The agent as it was published. A run is refused if the agent has since been turned off or deleted, and the Run dialog, the run preview and the version's page say which agents changed and how. |

**How approval steps behave**

| Situation | Behaviour |
|---|---|
| Asked | When every step it waits for has completed. The run shows **Needs approval**; other branches keep running, and an approval doesn't take one of the run's agent slots. |
| What you see | What to check (the step's instructions), the reports of the steps it waits for (looking through earlier approvals), the files changed so far with a link to **Changes**, and what approving or rejecting will do. |
| Approve | The step is done and the steps after it run. An optional note reaches them with the reports the approval checked. |
| Reject | The steps after it are marked skipped at once, with the reason. Independent branches finish. The run then ends *cancelled* ("Approve the plan was rejected: your note. Not run: …"), and the task goes back to Ready. If a step also failed, the run fails, naming both. |
| Cancel the run | Pending requests are cancelled; the step shows Cancelled. |
| Pause, shutdown, crash | The request stays pending and the step keeps waiting. A decision made meanwhile is recorded and starts nothing; **Resume** waits on the same request, or carries on with the decision. Nothing is asked twice. |
| Duplicates and stale pages | One transaction resolves the request and moves the step, and only from pending and waiting. A second click, another tab or a stale page is told the request was already decided. |
| Time limit | None: a request waits until someone decides or the run is cancelled. Plan approvals keep their 24 hours. |
| Retry and skip | Retrying a run doesn't re-ask a rejected approval; retrying that step does, when the run is failed, paused or interrupted. Approval steps can't be skipped. |
| Rules | A workflow needs at least one agent step. Warnings: an approval that waits for nothing, and one with nothing to check. |

**What changed from the plan**

- **"See what changed"** is a list of added, removed and changed steps and
  fields (in the publish dialog and on a version's page), not a drawn diff.
- **Approval steps persist** instead of being re-asked on resume like plan
  approvals; plan and tool approvals behave as before.
- **Skipped as soon as known.** In workflow runs, steps that can no longer run
  are marked skipped as soon as a step they wait for fails or is rejected,
  and the reason names the cause ("which failed", "which was rejected")
  rather than milestone 1's "which did not finish". Runs recorded before
  keep their wording.
- **Restart recovery** now emits an event for every step it puts back (team
  runs too), so the live graph matches the run after a restart, and notes
  "Interrupted by a Bosun restart" on the step until it runs again.
- **Found on the way:** tab panels had no visible focus, the sidebar's
  approvals button had a different name from its words, and the editor's
  tests ended with uncaught errors. All three predate this milestone and are
  fixed.

**Limits**

- Versions can't be renamed or deleted. Publishing needs a change to the
  steps, name, description or agents.
- **Restore to draft** replaces the draft after a confirmation; the draft's
  unpublished changes are lost unless published first.
- The Steps view draws the layout the run froze, so a workflow published
  without **Tidy up** can look tangled there.
- Decisions are recorded as "you": Bosun has one user.
- In narrow windows the Steps view's panel opens over the canvas.
- Still open: `aria-hidden-focus` while a menu is open (see VALIDATION's
  follow-ups); now eleven elements in the editor.

Implemented screens:

| Screen | Image |
|---|---|
| No workflows yet, with templates | ![](workflows/m2-templates.png) |
| A template's preview | ![](workflows/m2-template-preview.png) |
| An approval step in the editor | ![](workflows/m2-editor-approval.png) |
| The same, light theme | ![](workflows/m2-editor-approval-light.png) |
| Publishing a version | ![](workflows/m2-publish.png) |
| The Versions tab | ![](workflows/m2-versions.png) |
| A version, light theme | ![](workflows/m2-version-page-light.png) |
| A version that can't run now | ![](workflows/m2-version-blocked.png) |
| Restoring a version | ![](workflows/m2-restore.png) |
| Choosing what to run | ![](workflows/m2-run-dialog.png) |
| A workflow from the task's Run dialog | ![](workflows/m2-task-run-dialog.png) |
| A real run waiting at an approval | ![](workflows/m2-steps-approval-real.png) |
| The real run, finished | ![](workflows/m2-steps-done-real.png) |
| The Steps list, light theme | ![](workflows/m2-steps-list-light.png) |
| A rejected approval | ![](workflows/m2-steps-rejected.png) |
| After a crash, the approval still waiting | ![](workflows/m2-after-crash.png) |
| Approvals waiting for you | ![](workflows/m2-approvals-list.png) |
| 1024×768: the Steps view | ![](workflows/m2-steps-narrow-light.png) |
| 1024×768: the Workflows list | ![](workflows/m2-list-narrow-light.png) |
| A milestone 1 run in the Steps view | ![](workflows/m2-m1-run-steps.png) |
| Keyboard focus on Approve | ![](workflows/m2-keyboard-approve.png) |

### Milestone 3: unattended runs and their safeguards

Deployments exist, but runs only start when a person presses **Run now** and
walks away. Every safeguard is exercised before anything can start on a
timer. Details are in section 7; in summary:
- **Provider gate:** API key or Vertex by default (decision D1).
- **Agent environment:** credential variables stripped, with an explicit
  pass-through list.
- **Tools:** deny unlisted tool requests without waiting (D4), with a
  per-deployment allow-list shown at confirmation. Web tools, SDK subagents
  and `trusted_shell` are off. The OS sandbox is required where it can run
  (D3).
- **Limits:**
  - a wall-clock deadline per run;
  - a token ceiling per run, checked between turns;
  - a daily run cap;
  - one unattended run at a time.
- **Safe restart:**
  - a single-instance lock on the data folder;
  - the merge-crash fix, if it isn't done in milestone 1;
  - automatic resume only when it is provably safe, otherwise "needs review"
    (D5);
  - a "Pause all automation" switch.
- **Where unattended runs live:** decision D2. This is the only milestone
  that needs a decision on runs without tasks.

### Milestone 4: schedules

- Interval and cron schedules in a time zone, with a preview of the next
  times.
- Pause and resume.
- What happens to a run that overlaps the next one, and to times missed while
  Bosun was off.
- Retries with backoff.
- Auto-pause after repeated failures or a review backlog.
- A history that includes skipped times.

The scheduler design from revision 1 stands: database-driven, one ledger
row per scheduled time, compare-and-set on the next fire time, and the
instance lock from milestone 3.

### Later, once workflows have proved themselves

- Branching on an agent's declared outcome (structured output is already
  supported).
- Command steps.
- Bounded loops.
- A secrets manager.
- Cost and token budgets with dashboards.
- Notifications.
- MCP triggers.
- Import and export.
- **Rebuilding team mode as a built-in workflow:** deferred, so team mode
  keeps its current code path and tests until there is a reason to merge
  them.

---

## 6. The minimum architectural change

### Manual workflows (milestones 1 and 2): no run-model change

Runs stay exactly what they are: an `AgentRun` that belongs to a task. A
workflow adds the following:

| Change | Backward compatible because |
|---|---|
| `ExecutionMode.WORKFLOW` | New enum value; existing rows and requests are untouched |
| `RunConfig.workflow` (optional) | Missing on old runs; `RunConfig.model_validate` of stored configs is unaffected |
| `RunRequest.workflow_id` (optional) | Absent in every existing client |
| `workflows` table | New table, additive migration |
| `TeamExecutor.plan()`: if the config carries a workflow, write its steps instead of running the coordinator | The coordinator path is unchanged when there is no workflow |
| Workflow prompt variant; strict role check; skip marking | Only applied when `mode == workflow` |
| Merge-crash reconciliation on resume (D7) | Behaviour change for team runs too, but only in a crash window where today's behaviour is wrong |
| Reducer: coordinator-less plans | Only applies when there is no coordinator; team graphs unchanged (tested) |

Regression coverage required before merging milestone 1:
- **Unchanged expectations:** the full existing backend suite, including
  `test_runs.py`'s team, single, pause, resume, retry, cancel and recovery
  tests. They must pass without editing expectations.
- **Team graph fixtures:** the recorded team-run fixtures
  (`frontend/src/test/fixtures/team-run.json`, `spike-team-run.json`) must
  produce the same node and edge counts and kinds.
- **New explicit tests:**
  - a team run still plans through a coordinator and asks for plan approval;
  - `auto` mode still lets the coordinator choose;
  - a single-agent run is unchanged;
  - a stored `RunConfig` from before the migration loads;
  - one active run per task and idempotency keys still apply to workflow
    runs;
  - a resumed team run after a merge crash completes without re-running
    the merged subtask.

### Unattended runs (milestones 3 and 4): where do they live?

What the 163 references to `task_id`/`task_key` actually are, grouped by what
they do with the task. These counts are from `rg` in the files that handle
runs, *verified*:

| Use | Where | For a run without a task |
|---|---|---|
| Carry the ID into events and notifications | `runtime.py`, `team.py`, `manager.py`, `bus.py` (most references) | `ExecutionEvent.task_id` is already nullable; skip the `task` notification |
| Labels: branch name, commit messages, prompt header, run header | `git.py:183`, `manager.py`, `team.py` merge messages, `prompts.py`, `RunPage.tsx` | A subject label (deployment slug and name) |
| Task content for prompts | `build_task_context` | The deployment's inputs |
| Board lifecycle: status transitions, activity entries | `manager.py` start, finish, integrate, recover | Skip |
| Constraints: one active run per task, run numbering, archived check, workspace from task | `manager.py:start`, partial index | One active run per deployment; numbering per deployment; workspace from the deployment |
| Pull-request source | `_pull_request_source` | Skip |
| Serialisation and lists | `serialize.py`, `routes/runs.py`, `dashboard.py`, MCP | Optional task fields (lists already fall back to "?") |
| Hard assumptions that would crash | `integrate()` asserts the task exists; `RunOut.task_id: str` | Guard them |

So the change is about 10 backend files and 5 frontend files, concentrated in
`RunManager`. It is not a rewrite.

Options:

| | A: a new task per unattended run | B: runs without a task (nullable `task_id`, a run "subject") | C: one task per deployment |
|---|---|---|---|
| Run-model change | None; the scheduler creates a task, then starts a normal run | `task_id` nullable, about 10 files guarded, a migration that rebuilds `agent_runs` (SQLite) | None |
| Board | One card per run (a daily schedule adds 7 cards a week) | Untouched; results show on the deployment page and Overview | One card that keeps moving |
| Fit with "Done is a human decision" | Good: each run gets its own card to close | Good | **Breaks:** the system can never move a Done task, so marking the card Done stops the schedule |
| Review and merge | The existing flow | The existing run page; the board doesn't list them | The existing flow, but each run's result replaces the last |

**Recommendation: B**, because unattended runs are operations rather than
work items, and A floods the board at any useful frequency. **A is the
smaller and fully backward-compatible choice** if you would rather review
scheduled results on the board. Decision D2, needed before milestone 3.
Regression coverage for B adds: every run endpoint and every list with a
run that has no task; `integrate`, `recover` and dashboard queries with such
a run; task runs unchanged.

---

## 7. Recovery, duplicates and side effects

### What recovery does today (*verified*)

- **On a clean shutdown**, active runs are stopped and marked `interrupted`.
- **After a crash**, `RunManager.recover()` does the same at the next start:
  1. sessions that were pending, running or waiting become `interrupted`;
  2. tagged `claude` processes that survived are killed;
  3. running subtasks go back to `pending`;
  4. pending approvals are expired.

  Nothing starts again on its own.
- **Resume is a person's action.** It continues each interrupted Claude
  session with the SDK's `resume` and the "continue from where you left off"
  prompt. Completed steps are not repeated, a plan approval that was pending
  is asked again, and tool approvals are not re-asked: the agent repeats the
  request if it still needs it.
- **Retry** re-queues a failed run and resets failed, cancelled and
  conflicting steps.

**Automatic resume** (milestone 3) would make Bosun call that same resume
itself after `recover()`. It would apply only to unattended runs, and only
when the conditions below hold.

### Two separate guarantees

1. **At most one run record per trigger.** This is enforced in the database:
   - manual starts use an idempotency key, and a partial unique index allows
     one active run per task (and per deployment, later);
   - scheduled starts use one ledger row per `(schedule, scheduled time)`
     plus compare-and-set on `next_fire_at`;
   - a single-instance lock stops two Bosun processes from both running
     schedules and recovery.

   This guarantee is strong and testable.
2. **Safe execution of side effects.** There is no exactly-once guarantee.
   A step interrupted mid-session is continued, so the action that was in
   flight may happen twice. Bosun makes **its own** operations idempotent or
   reconciles them on resume. It **constrains** what agents can do so that
   repeating is harmless, and it **stops for a person** when it can't tell.

### Crash windows

| Operation | Repeatable? | If the process dies after it succeeds but before it is recorded | Change |
|---|---|---|---|
| Create run, move task to In progress | Two commits | Run exists, task not moved; recovery marks the run interrupted | Single transaction |
| `git worktree add` for the run | Yes: path and branch are deterministic and an existing path is reused | Reused on resume | None |
| An agent's tool call in its worktree | File edits: usually. Shell commands: depends on the command | The session is resumed; the agent may repeat the last call | Unattended allow-lists contain only commands that are safe to repeat (tests, linters, builds) |
| Commit a step's changes in its child worktree | Yes: no-op when clean | — | None |
| A step's session completes, before its child commit and merge | — | Resume finds a completed (not interrupted) session, deletes the child worktree, and re-runs the step: **its work is lost and redone** (*verified* by reading `create_child` and the resume path) | Commit and merge the existing child worktree instead of re-running |
| Merge a step's child branch into the run branch | Merging an already-merged branch is a no-op | The step re-runs on top of its own merged work and **non-idempotent edits apply twice** (*verified*, spike test) | Record a "merging" marker before merging; on resume, if the child branch is an ancestor of the run branch, mark the step complete with its recorded report |
| Final commit on the run branch | Yes | Resume reaches an empty schedule and summarises again | None (costs one summary session for teams) |
| Merge into your branch (you confirmed it) | — | `integration_status` stays `pending`; pressing Merge again fails with "The agent branch has no commits that are not already on your branch" (*verified*, `WorktreeManager.integrate` in `orchestration/git.py`) | Detect a branch already contained in yours and mark it merged |
| Record an approval decision | Atomic `UPDATE … WHERE status = 'pending'` | — | None |

### When a person must review

An unattended run is **not** auto-resumed, and is marked "needs review" with
the reason, when any of these hold:
- an interrupted session's last tool call has no recorded result and it was
  outside the deployment's repeat-safe allow-list (detectable from
  `tool.call` / `tool.result` events);
- a step was between merging and being recorded, and reconciliation can't
  prove the merge landed;
- an integration conflict is pending;
- the run passed its deadline;
- it was already auto-resumed once.

### Defined behaviour

| Situation | Behaviour |
|---|---|
| A trigger fires while the deployment's previous run is active | Default: **skip**, recorded in history. Option: **run once it finishes** (at most one waiting). Never two at once. |
| Bosun was off or asleep at a scheduled time | Default: run once for the most recent missed time if it is within one interval; record the others as missed. Option: skip them all. |
| A run passes its deadline | Stopped like a cancel; the run fails with `timeout`; the worktree is kept for review; counts as a failure for auto-pause. |
| A step passes its own time limit | The session ends with `timeout` (time waiting for approval excluded, as today); retried if retries remain, else the step fails. |
| A person cancels | Cancel wins over pause; sessions stop; the run is `cancelled`; the worktree is kept and can be discarded; it doesn't count as a failure; the schedule continues unless "Cancel and pause" is chosen. |
| An approval is pending when the process dies | It is expired at start-up. On resume, plan and approval-step requests are asked again; tool requests are not, and the agent may ask again. For unattended runs, any tool request outside the allow-list is denied at once, so none can be pending. |
| An approval times out | Treated as deny (as today). Integration conflicts stay in `conflict` for a person. |
| Non-retryable failures (signed out, billing guard, sandbox unavailable, CLI missing) | No retry; the deployment pauses with the reason and the fix. `usage_limit` is non-retryable within a run *(verified, `ErrorKind.NON_RETRYABLE`)*. The scheduler waits for the reset time before the next run instead of retrying. |

---

## 8. UI and UX

### What exists and is reused

| Need | Reuse | Notes |
|---|---|---|
| Page frame, title, actions | `PageHeader`, the run page's back-arrow header | Same type scale (`text-lg` titles, `text-sm` subtitles) |
| Tabs (Editor, Runs; later Versions, Deployments) | `Tabs`, `TabPanel` | Same as the run page |
| Buttons | `Button` variants primary, secondary, ghost, outline, danger; sizes xs to md and icon | Run is the one primary action per screen |
| Forms | `Field`, `Input`, `Textarea`, `NativeSelect`, `Switch`, `Segmented` | `Field` gives label and hint consistently |
| Overlays | `Dialog` (run dialog, schedule), `DropdownMenu` (add a step), `Tip`, `Sheet` | The Run dialog follows `LaunchDialog`'s preview pattern |
| Status | `StatusPill`, `STATE` (icon, label, pattern, colour), `StateDot` | Status never relies on colour alone |
| Empty, error, notice | `EmptyState`, `ErrorNote`, `Notice` | |
| Key facts | `KeyValue`, `RoleTag`, role colours (`roleColor`) | |
| Canvas | React Flow setup from `AgentGraph` (`canvas-grid`, dot background, theme variables in `index.css`), node look from `nodes.tsx` (role-colour edge, icon, title) | |
| Focus | Global `:focus-visible` ring in `index.css` | |
| Toasts | `sonner` | "Run 4 started… Open run" |

New reusable patterns:
- **StepNode:** an editable node, larger than run-graph nodes, with
  connectable handles, a focus ring, and badges that use an icon plus a word.
- **Canvas toolbar:** the Add a step menu, plus zoom, fit and tidy controls.
- **Inspector panel:** a right-hand panel with progressive disclosure. It
  becomes an overlay below about 1180px.
- **Problems panel:** docked below the canvas.
- **SaveStatus:** shows Saved, Saving… and Not saved, retrying.

Two small fixes found while building the mockups, in shared components:
- `NativeSelect` renders on the panel colour while `Input` uses the sunken
  colour. This shows today in the Inbox toolbar and in Settings.
- `StateIcon` in `nodes.tsx` isn't exported; it should be shared rather than
  re-created.

### Editor design

- **Layout:** top-down. Steps flow downward, parallel branches sit side by
  side, and joins are visible where edges meet. Top-down fits the tall,
  narrow canvas left over beside the inspector. A left-to-right first
  attempt, with a palette, fitted the graph at about 40% zoom, which made the
  step text unreadable at 1440×900.
- **Readable at any size:** fitting the view never zooms below 90%; the
  canvas pans instead. Steps are 232×76 with `text-sm` titles. "Board task"
  and "Result for review" anchors frame the flow, matching the run graph.
- **Creating a step:**
  - **Add a step** opens a menu of agents, each with its access level;
  - the **+** under a selected or hovered step adds one after it;
  - dragging from a step's bottom edge onto empty canvas creates a step (not
    built in milestone 1);
  - the keyboard shortcut is `A`.
- **Connecting:** drag from a step's bottom handle to another step. The
  **Runs after** list in the inspector does the same without a mouse
  (**Wait for another step**). Creating a cycle is refused at once, with a
  message saying why.
- **Selecting:** click, or `Tab` to a step and press `Enter`. `Esc` clears
  the selection. (As built, the arrow keys move the selected step, as in
  React Flow, rather than jumping between connected steps.)
- **Configuring:** the inspector shows the name, agent (with its permission
  summary from `tool_policy`), instructions, and "Changes code". **Advanced**
  is collapsed, showing a one-line summary such as "Agent defaults, 1 retry",
  and holds the model, max turns, time limit and retries.
- **Deleting:** the `Delete` key or the bin icon. A toast offers Undo. Undo
  and redo cover every canvas edit (`Ctrl+Z`, `Ctrl+Shift+Z`).
- **Validation feedback:**
  - steps carry a **Fix** (problem) or **Check** (warning) badge, each with
    an icon;
  - the header shows "2 problems, 1 warning";
  - the docked problems panel lists them; clicking one selects its step;
  - Run is disabled, with the reason in a tooltip and beside it;
  - inspector fields with a problem show it under the field.
- **Save status:** next to the title. Not saved retries automatically and
  offers Retry now. A conflict offers Load theirs or Keep mine.
- **Execution status:**
  - on the run page, every step shows its status icon and its label (Done,
    Running, Needs approval, Waiting on dependency, Skipped);
  - approvals appear next to the step that needs them (Steps tab,
    milestone 2);
  - starting a run confirms with a toast that links to it.
- **Accessibility:**
  - text uses the existing tokens, which meet WCAG AA (checked with axe-core
    in milestone 1, which found the light theme's status colours and muted
    text short of it; both were darkened);
  - focus is visible on every control, including steps;
  - every status and issue pairs an icon with a word;
  - steps have accessible names ("Step Review, no agent, has a problem");
  - the inspector is a labelled region;
  - motion follows the existing reduced-motion handling.
- **Smaller windows and larger graphs:**
  - **Narrow windows:** at 1024px the inspector becomes an overlay that can
    be dismissed, and the canvas pans to keep the selected step clear of it.
    The sidebar stays as you left it (it doesn't collapse on its own).
  - **Size limit:** a workflow is limited to 20 steps.
  - **Navigation aids:** above 12 steps, the minimap appears, and Tidy up
    re-lays the graph out with dagre.

### Mockups

Built from Bosun's own components (`93d0a48`), rendered by the app at
1440×900 unless noted.

| Screen | Image |
|---|---|
| Workflows list | ![](workflows/mock-workflows.png) |
| No workflows yet | ![](workflows/mock-workflows-empty.png) |
| Editor, step selected | ![](workflows/mock-editor.png) |
| Editor, dark theme | ![](workflows/mock-editor-dark.png) |
| Add a step | ![](workflows/mock-editor-add.png) |
| Step settings, advanced open, saving | ![](workflows/mock-editor-configure.png) |
| Validation feedback | ![](workflows/mock-editor-issues.png) |
| Validation feedback, dark | ![](workflows/mock-editor-issues-dark.png) |
| Empty workflow | ![](workflows/mock-editor-empty.png) |
| Loading | ![](workflows/mock-editor-loading.png) |
| Could not load | ![](workflows/mock-editor-error.png) |
| Run started | ![](workflows/mock-editor-started.png) |
| 1024×768, sidebar collapsed, inspector as overlay | ![](workflows/mock-editor-narrow.png) |
| Run dialog | ![](workflows/mock-run-dialog.png) |
| Run inspection, Steps tab (milestone 2) | ![](workflows/mock-run-steps.png) |
| Deployment (milestone 3) | ![](workflows/mock-deployment.png) |
| Schedule (milestone 4) | ![](workflows/mock-schedule.png) |

Known issues in the mockups, to fix during implementation:
- the schedule dialog shows a 12-hour time where the rest of the app uses 24
  hours (follow the locale consistently);
- in the run dialog the summary scrolls below the fold at 900px tall.

### States

| Screen | Empty | Loading | Error | Disabled | Success |
|---|---|---|---|---|---|
| Workflows | New workflow | Row skeletons | `ErrorNote` with Try again | — | New workflow opens the editor |
| Editor | "Add the first step"; Run disabled with "Add a step to run" | Canvas and inspector skeletons | `ErrorNote`; draft is safe on the server | Run disabled with the reason | Saved; run-started toast |
| Run dialog | No tasks: New task is selected | Tasks loading; billing "Checking…" | The server's refusal, scrolled into view | Start disabled until the task has a title, and while changes are saving | Dialog closes; toast with Open run |
| Run page | — | Existing | Plain-language failure naming the step | Merge disabled while active (existing) | Ready for review (existing) |
| Deployment | "Run now or add a schedule" | Existing patterns | Paused banner with reason and fix | Run now disabled while a run is active | History row |

### Visual review in every milestone

For each new or changed screen:
1. **Screenshots** at 1440×900 and 1024×768, in light and dark, next to the
   Board, Inbox and run page.
2. **Consistency check:** typography, spacing, radii, colours (tokens only),
   button variants, form layout, terminology, and that there's one primary
   action per screen.
3. **Keyboard-only walk-through** of the milestone's main journey, with the
   focus visible throughout.
4. **Automated check:** an accessibility check (axe-core through Playwright)
   with no serious violations other than known, recorded ones.
5. **Console:** no console errors.
6. **Sign-off:** your review.

---

## 9. Risks and decisions

### Risks

- **Subscription terms** (D1). Unattended use on a consumer plan isn't
  explicitly permitted for third-party tools; Anthropic says it may enforce
  "without prior notice".
- **Engine coupling.** Workflows and teams share `TeamExecutor`, so a
  workflow change can break teams. Mitigation: the regression list in
  section 6, and workflow-only branches keyed on the mode.
- **Agent side effects can't be made exactly-once.** Mitigation: repeat-safe
  allow-lists, the sandbox, and review on doubt.
- **Editor scope:** undo, autosave, conflicts, keyboard and accessibility.
  Mitigation: one store with command-based undo; no realtime collaboration.
- **Disk growth** from per-run worktrees once runs are unattended.
  Mitigation: clean up runs without changes, and the review backlog cap.
- **Agents inherit the service's environment** (except `BOSUN_*` and billing
  variables; *verified* in `agents/providers.py`). Today that environment
  holds no credentials, *verified* on this machine. Anything exported in
  your login shell would reach agents. Mitigation: scrubbing in milestone 3.

### Provider restrictions (*verified* 2026-10-20 against Anthropic's documentation)

- **Legal and compliance:** the
  [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
  page says:
  - "Advertised usage limits for Pro and Max plans assume ordinary,
    individual usage of Claude Code and the Agent SDK."
  - "Developers building products or services that interact with Claude's
    capabilities, including those using the Agent SDK, should use API key
    authentication… Anthropic does not permit third-party developers to
    offer Claude.ai login into their own applications, or to route requests
    through Free, Pro, or Max plan credentials on behalf of their users."
  - "Nor does it prevent an end user from signing in to the unmodified
    Claude Code binary with their own Claude subscription."
  - "Anthropic reserves the right to take measures to enforce these
    restrictions and may do so without prior notice."
- **Consumer Terms:** the
  [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
  (effective 8 October 2025), section 3(7), prohibit accessing the Services
  "through automated or non-human means, whether through a bot, script, or
  otherwise", "except when you are accessing our Services via an Anthropic
  API Key or where we otherwise explicitly permit it".
- **What Anthropic explicitly offers:**
  - [Routines](https://code.claude.com/docs/en/routines), which run on
    Anthropic's cloud;
  - [Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks);
  - [`claude setup-token`](https://code.claude.com/docs/en/authentication),
    a subscription token "for CI pipelines, scripts, or other environments
    where interactive browser login isn't available".
- **What isn't covered:** none of these mention a third-party scheduler
  driving the Agent SDK on a subscription.
- **Reading** (not legal advice): a person starting each run, as Bosun does
  today and in milestones 1 and 2, fits "ordinary, individual usage" with
  their own login in the unmodified CLI. Unattended runs started by Bosun's
  scheduler are automated access that the documentation does not explicitly
  permit for third-party tools. News coverage from February 2026 quotes an
  earlier, stricter wording that named the Agent SDK directly; the current
  page reads as above.

### Decisions

Ordered by milestone. ⚠ marks the ones that materially affect security, cost
or behaviour.

| ID | Decision | Recommendation | Needed by |
|---|---|---|---|
| D7 | Fix the merge-crash re-run in milestone 1 (it affects team runs today) | Yes (done) | M1 |
| D8 | Failure semantics: keep "independent branches finish, then the run fails" | Keep, and show it clearly; a "stop everything on first failure" option later (done: kept) | M1 |
| D9 | Names: "Workflows" in the sidebar after Board; "steps" for nodes | Yes (done) | M1 |
| D1 ⚠ | Unattended runs on the Claude subscription | **No by default**: unattended runs require an API key or Vertex. If you choose to allow the subscription, it should be an explicit, per-deployment acknowledgement citing the terms above | M3 |
| D2 ⚠ | Where unattended runs live: B (no task) or A (a new task per run) | B | M3 |
| D3 ⚠ | Require the OS sandbox (bubblewrap and socat on Linux, Seatbelt on macOS) for unattended runs | Yes where available. In Docker, where it can't run, allow only read-only agents unattended | M3 |
| D4 ⚠ | Unattended tool requests outside the allow-list | Deny immediately; no waiting | M3 |
| D5 ⚠ | Automatic resume after a restart | Once, only under the conditions in section 7; otherwise "needs review" | M3 |
| D6 ⚠ | Default limits | Deadline 2 h per run; 400k tokens per run; 3 unattended runs a day per deployment; one unattended run at a time; schedules no more often than hourly | M3–M4 |

### Assumptions

- One user, one machine, one Bosun process per data folder.
- Workflows stay at 20 steps or fewer.
- The demo adapter is a faithful stand-in for orchestration behaviour (it
  was for the existing team tests). Real Claude runs are part of milestone 1
  acceptance.
- axe-core can run against the app for the visual review (in milestone 1 it
  ran from a scratch folder rather than as a dependency).
