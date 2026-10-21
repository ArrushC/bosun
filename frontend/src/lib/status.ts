import {
  AlertOctagon,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock4,
  Hand,
  Link2,
  Loader2,
  type LucideIcon,
  PauseCircle,
  PlugZap,
  SkipForward,
} from "lucide-react";

/**
 * Canonical visual states shared by the board, graph, timeline and tables.
 * Colour is always paired with an icon and a pattern so state never depends on
 * colour alone.
 */
export type VisualState =
  | "pending"
  | "running"
  | "waiting_dependency"
  | "waiting_approval"
  | "success"
  | "failed"
  | "cancelled"
  | "skipped"
  | "paused";

export interface StateMeta {
  label: string;
  color: string; // CSS colour (var)
  icon: LucideIcon;
  spin?: boolean;
  pattern: "solid" | "dashed" | "pulse" | "sweep" | "struck";
}

export const STATE: Record<VisualState, StateMeta> = {
  pending: { label: "Pending", color: "var(--st-idle)", icon: CircleDashed, pattern: "dashed" },
  running: { label: "Running", color: "var(--st-run)", icon: Loader2, spin: true, pattern: "sweep" },
  waiting_dependency: { label: "Waiting on dependency", color: "var(--st-dep)", icon: Link2, pattern: "dashed" },
  waiting_approval: { label: "Needs approval", color: "var(--st-approval)", icon: Hand, pattern: "pulse" },
  success: { label: "Done", color: "var(--st-ok)", icon: CheckCircle2, pattern: "solid" },
  failed: { label: "Failed", color: "var(--st-fail)", icon: AlertOctagon, pattern: "solid" },
  cancelled: { label: "Cancelled", color: "var(--st-cancel)", icon: Ban, pattern: "struck" },
  skipped: { label: "Skipped", color: "var(--st-cancel)", icon: SkipForward, pattern: "dashed" },
  paused: { label: "Paused", color: "var(--st-dep)", icon: PauseCircle, pattern: "dashed" },
};

export function runState(status: string | null | undefined): VisualState {
  switch (status) {
    case "queued":
      return "pending";
    case "preparing":
    case "planning":
    case "running":
      return "running";
    case "waiting_approval":
      return "waiting_approval";
    case "completed":
      return "success";
    case "failed":
      return "failed";
    case "paused":
    case "interrupted":
      return "paused";
    case "cancelled":
      return "cancelled";
    default:
      return "pending";
  }
}

export function sessionState(status: string | null | undefined): VisualState {
  switch (status) {
    case "running":
      return "running";
    case "waiting_approval":
      return "waiting_approval";
    case "completed":
      return "success";
    case "failed":
      return "failed";
    case "interrupted":
      return "paused";
    case "cancelled":
      return "cancelled";
    default:
      return "pending";
  }
}

export function subtaskState(status: string | null | undefined): VisualState {
  switch (status) {
    case "waiting_dependency":
      return "waiting_dependency";
    case "running":
      return "running";
    case "waiting_approval":
      return "waiting_approval";
    case "completed":
      return "success";
    case "failed":
    case "conflict":
      return "failed";
    case "skipped":
      return "skipped";
    case "cancelled":
      return "cancelled";
    default:
      return "pending";
  }
}

export const RUN_LABEL: Record<string, string> = {
  queued: "Queued",
  preparing: "Preparing",
  planning: "Planning",
  running: "Running",
  waiting_approval: "Needs approval",
  paused: "Paused",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

export const TASK_COLUMNS = [
  { id: "backlog", label: "Backlog", icon: CircleDashed },
  { id: "ready", label: "Ready", icon: CircleDot },
  { id: "in_progress", label: "In progress", icon: Loader2 },
  { id: "review", label: "Review", icon: Clock4 },
  { id: "blocked", label: "Blocked", icon: PlugZap },
  { id: "done", label: "Done", icon: CheckCircle2 },
] as const;

export const PRIORITY: Record<string, { label: string; level: number; color: string }> = {
  urgent: { label: "Urgent", level: 4, color: "var(--st-fail)" },
  high: { label: "High", level: 3, color: "var(--st-approval)" },
  medium: { label: "Medium", level: 2, color: "var(--text-2)" },
  low: { label: "Low", level: 1, color: "var(--text-3)" },
  none: { label: "No priority", level: 0, color: "var(--text-3)" },
};

export const KIND_LABEL: Record<string, string> = {
  implementation: "Implementation",
  bugfix: "Bug fix",
  investigation: "Investigation",
  review: "Review",
  testing: "Testing",
  refactor: "Refactor",
  chore: "Chore",
};

export const ROLE_COLOR: Record<string, string> = {
  amber: "var(--role-amber)",
  sky: "var(--role-sky)",
  violet: "var(--role-violet)",
  emerald: "var(--role-emerald)",
  rose: "var(--role-rose)",
  slate: "var(--role-slate)",
};

const ROLE_DEFAULT_COLOR: Record<string, string> = {
  coordinator: "amber",
  researcher: "sky",
  implementer: "violet",
  tester: "emerald",
  reviewer: "rose",
};

export function roleColor(role: string | null | undefined, color?: string | null): string {
  return ROLE_COLOR[color ?? ROLE_DEFAULT_COLOR[role ?? ""] ?? "slate"] ?? ROLE_COLOR.slate;
}

/** How a run executes, in words: "Workflow", "Agent team", "Single agent"… */
export function strategyLabel(run: { mode: string; strategy?: string | null }): string {
  if (run.mode === "workflow") return "Workflow";
  if (run.strategy === "team" || run.mode === "team") return "Agent team";
  if (run.mode === "auto") return run.strategy === "single" ? "Auto: single agent" : "Auto: planning";
  return "Single agent";
}

/** Short names for agents' permission profiles. */
export const PROFILE_LABEL: Record<string, string> = {
  read_only: "Read only",
  edit: "Edit files, ask before shell",
  edit_and_test: "Edit files and run tests",
  trusted_shell: "Edit files and run any command",
};

/** What each profile lets an agent do (the wording the backend's tool policy uses). */
export const PROFILE_DESCRIPTION: Record<string, string> = {
  read_only: "Read-only: can read and search files in its worktree.",
  edit: "Can edit files in its worktree. Shell commands outside the safe list need your approval.",
  edit_and_test: "Can edit files and run configured test commands. Other shell commands need approval.",
  trusted_shell: "Can edit files and run any shell command in its worktree without asking.",
};
