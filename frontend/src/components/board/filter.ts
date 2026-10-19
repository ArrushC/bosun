import type { Task, TaskStatus } from "@/api/types";
import { PRIORITY } from "@/lib/status";
import type { GroupBy, SortBy } from "@/stores/ui";

export interface BoardFilterState {
  q: string;
  labels: string[];
  priorities: string[];
  workspaceId: string | null;
  sources: string[];
  agentState: "any" | "running" | "attention";
  showDone: boolean;
}

const ACTIVE_RUN = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

export function taskSourceKind(task: Task): string {
  return task.sources[0]?.kind ?? "manual";
}

export function needsAttention(task: Task): boolean {
  const run = task.latest_run;
  return !!run && (run.pending_approvals > 0 || run.status === "failed" || run.status === "waiting_approval" || run.status === "interrupted");
}

export function filterTasks(tasks: Task[], f: BoardFilterState): Task[] {
  const q = f.q.trim().toLowerCase();
  return tasks.filter((t) => {
    if (!f.showDone && t.status === "done") return false;
    if (q) {
      const hay = `${t.key} ${t.title} ${t.description} ${t.labels.join(" ")} ${t.sources.map((s) => `${s.path ?? ""} ${s.repo ?? ""}#${s.number ?? ""}`).join(" ")}`.toLowerCase();
      if (!q.split(/\s+/).every((word) => hay.includes(word))) return false;
    }
    if (f.labels.length && !f.labels.every((l) => t.labels.includes(l))) return false;
    if (f.priorities.length && !f.priorities.includes(t.priority)) return false;
    if (f.workspaceId && t.workspace_id !== f.workspaceId) return false;
    if (f.sources.length && !f.sources.includes(taskSourceKind(t))) return false;
    if (f.agentState === "running" && !(t.latest_run && ACTIVE_RUN.has(t.latest_run.status))) return false;
    if (f.agentState === "attention" && !needsAttention(t)) return false;
    return true;
  });
}

export function sortTasks(tasks: Task[], sortBy: SortBy): Task[] {
  const sorted = [...tasks];
  switch (sortBy) {
    case "priority":
      return sorted.sort((a, b) => (PRIORITY[b.priority]?.level ?? 0) - (PRIORITY[a.priority]?.level ?? 0) || a.position - b.position);
    case "updated":
      return sorted.sort((a, b) => (b.last_activity_at ?? b.updated_at).localeCompare(a.last_activity_at ?? a.updated_at));
    case "created":
      return sorted.sort((a, b) => b.created_at.localeCompare(a.created_at));
    default:
      return sorted.sort((a, b) => a.position - b.position);
  }
}

export function groupKey(task: Task, groupBy: GroupBy): string {
  switch (groupBy) {
    case "priority":
      return PRIORITY[task.priority]?.label ?? "No priority";
    case "workspace":
      return task.workspace_name ?? "No workspace";
    case "kind":
      return task.kind;
    default:
      return "";
  }
}

export function columns(tasks: Task[], sortBy: SortBy): Record<TaskStatus, Task[]> {
  const out: Record<TaskStatus, Task[]> = { backlog: [], ready: [], in_progress: [], review: [], blocked: [], done: [] };
  for (const t of sortTasks(tasks, sortBy)) out[t.status as TaskStatus]?.push(t);
  return out;
}

/** Neighbours around `index` in a column after a drop, for fractional positioning. */
export function neighbours(ids: string[], movedId: string): { before_id: string | null; after_id: string | null } {
  const index = ids.indexOf(movedId);
  return {
    after_id: index > 0 ? ids[index - 1] : null,
    before_id: index >= 0 && index < ids.length - 1 ? ids[index + 1] : null,
  };
}
