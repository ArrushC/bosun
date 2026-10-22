import type { Approval, Run, Session, Subtask } from "@/api/types";
import { stepStatusLabel, subtaskState, type VisualState } from "@/lib/status";
import { elapsed, formatDuration, plural } from "@/lib/utils";
import { executionOrder, normalizeStep, runWorkflow, type Step } from "@/lib/workflow";

/** One step of a workflow run: how the run defined it, and what has happened to it. */
export interface StepRow {
  step: Step;
  /** Its row in the run's plan; missing until the run has written the plan. */
  subtask: Subtask | undefined;
  /** Its agent sessions, oldest first: one per attempt or resume. */
  sessions: Session[];
  /** Its latest approval request (approval steps). */
  approval: Approval | undefined;
  state: VisualState;
  label: string;
  /** Why it is in that state, in plain words. */
  reason: string | null;
}

const STOPPED = new Set(["paused", "interrupted"]);

/** The run's steps in execution order, each with its status and the reason for it. */
export function stepRows(run: Run): StepRow[] {
  const workflow = runWorkflow(run);
  if (!workflow) return [];
  const steps = executionOrder(workflow.steps.map(normalizeStep));
  const byKey = new Map(run.subtasks.map((s) => [s.key, s]));
  const titles = new Map(steps.map((s) => [s.key, s.title.trim() || "Untitled step"]));
  return steps.map((step) => {
    const subtask = byKey.get(step.key);
    const sessions = subtask ? run.sessions.filter((s) => s.subtask_id === subtask.id) : [];
    const approval = subtask ? [...run.approvals].reverse().find((a) => a.subtask_id === subtask.id && a.kind === "workflow_step") : undefined;
    const row: StepRow = { step, subtask, sessions, approval, state: "pending", label: "Not started", reason: null };
    return { ...row, ...describe(run, row, byKey, titles) };
  });
}

function describe(
  run: Run,
  { subtask, sessions }: StepRow,
  byKey: Map<string, Subtask>,
  titles: Map<string, string>,
): Pick<StepRow, "state" | "label" | "reason"> {
  const stopped = STOPPED.has(run.status);
  const ended = !run.active && !stopped;
  if (!subtask) {
    return { state: "pending", label: "Not started", reason: ended ? "The run ended before its steps were planned." : "Starts once the run has prepared its worktree." };
  }
  const state = subtaskState(subtask.status);
  const label = stepStatusLabel(subtask.status, subtask.kind);
  const approval = subtask.kind === "approval";
  const last = sessions[sessions.length - 1];
  switch (subtask.status) {
    case "pending":
      if (stopped) {
        return {
          state: "paused",
          label: "Paused",
          reason: `${subtask.error === "Interrupted by a Bosun restart" ? "Interrupted by a restart. " : ""}Continues when you resume the run.`,
        };
      }
      if (ended) return { state: "pending", label: "Not run", reason: "The run ended before it started." };
      return {
        state,
        label: "Starting",
        reason: subtask.attempts > 0 && subtask.error ? `Trying again after: ${subtask.error}` : "Starts as soon as an agent is free.",
      };
    case "waiting_dependency": {
      const waiting = subtask.depends_on.filter((k) => byKey.get(k)?.status !== "completed").map((k) => titles.get(k) ?? k);
      const reason = waiting.length ? `Waits for ${join(waiting)}.` : "Waits for the steps before it.";
      return { state, label, reason: stopped ? `${reason} The run is ${run.status}.` : reason };
    }
    case "running":
      return { state, label, reason: subtask.max_attempts > 1 ? `Attempt ${subtask.attempts} of ${subtask.max_attempts}.` : null };
    case "waiting_approval":
      if (approval) {
        return { state, label, reason: stopped ? `Waiting for your decision. The run is ${run.status}: decide now, and it carries on when you resume.` : "Waiting for your decision." };
      }
      return { state, label, reason: "The agent asked for permission. See the request at the top of the page." };
    case "completed":
      if (approval) return { state, label, reason: subtask.report && subtask.report !== "Approved." ? subtask.report.replace(/^Approved\. /, "") : null };
    {
      const took = last?.started_at && last.ended_at ? `Took ${formatDuration(elapsed(last.started_at, last.ended_at))}` : null;
      const parts = [took, subtask.attempts > 1 ? plural(subtask.attempts, "attempt") : null].filter(Boolean);
      return { state, label, reason: parts.length ? `${parts.join(", ")}.` : null };
    }
    case "rejected":
      return { state, label, reason: subtask.error && subtask.error !== "Rejected." ? subtask.error.replace(/^Rejected: /, "Your note: ") : "You rejected it." };
    case "skipped":
      return { state, label, reason: subtask.error ?? "Skipped." };
    case "cancelled":
      return { state, label, reason: subtask.error ?? "Cancelled with the run." };
    default:
      return { state, label, reason: subtask.error ?? null };
  }
}

function join(names: string[]): string {
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The step to show first: one waiting for you, then one running, then one that failed. */
export function defaultStep(rows: StepRow[]): string | null {
  const pick = (states: VisualState[]) => rows.find((r) => states.includes(r.state))?.step.key;
  return pick(["waiting_approval"]) ?? pick(["running"]) ?? pick(["failed", "rejected"]) ?? null;
}
