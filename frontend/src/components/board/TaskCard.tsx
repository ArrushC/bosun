import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Hand, Link2, ListTree, TriangleAlert } from "lucide-react";
import { memo } from "react";

import type { Task } from "@/api/types";
import { PriorityIcon, SourceIcon } from "@/components/ui/display";
import { roleColor, runState, sessionState, STATE, type VisualState } from "@/lib/status";
import { cn, relativeTime } from "@/lib/utils";

const ACTIVE_RUN = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

/** The card's left edge carries the agent state — the flight-strip colour band. */
function edgeState(task: Task): VisualState | null {
  const run = task.latest_run;
  if (run && (run.pending_approvals > 0 || run.status === "waiting_approval")) return "waiting_approval";
  if (run && ACTIVE_RUN.has(run.status)) return "running";
  if (task.open_dependencies > 0 && task.status !== "done") return "waiting_dependency";
  if (run?.status === "failed") return "failed";
  if (run?.status === "completed" && task.status === "review") return "success";
  return null;
}

function sourceRef(task: Task): string | null {
  const s = task.sources[0];
  if (!s) return null;
  if (s.repo) return `${s.repo.split("/")[1] ?? s.repo}#${s.number}`;
  if (s.path) return `${s.path.split("/").slice(-2).join("/")}:${s.line}`;
  return null;
}

export const TaskCardView = memo(function TaskCardView({ task, onOpen, dragging, overlay, focusable = true }: {
  task: Task;
  onOpen?: () => void;
  dragging?: boolean;
  overlay?: boolean;
  focusable?: boolean;
}) {
  const edge = edgeState(task);
  const run = task.latest_run;
  const ref = sourceRef(task);
  const active = run && ACTIVE_RUN.has(run.status);

  return (
    <article
      role={focusable ? "button" : undefined}
      tabIndex={focusable ? 0 : undefined}
      aria-label={`${task.key} ${task.title}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen?.();
      }}
      className={cn(
        "group relative flex cursor-pointer overflow-hidden rounded-[8px] border border-line-soft bg-panel transition-[border-color,box-shadow] select-none hover:border-line focus-visible:border-accent",
        dragging && "opacity-30",
        overlay && "rotate-[0.6deg] border-line shadow-pop",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "w-[3px] shrink-0",
          edge === "running" && "state-edge-running",
          edge === "waiting_dependency" && "state-edge-dependency",
          edge === "waiting_approval" && "animate-pulse-ring",
        )}
        style={edge && edge !== "running" && edge !== "waiting_dependency" ? { background: STATE[edge].color } : edge ? undefined : { background: "var(--line-soft)" }}
      />
      <div className="min-w-0 flex-1 px-2.5 pt-2 pb-2">
        <div className="flex items-center gap-1.5 text-2xs text-text-3">
          <span className="mono">{task.key}</span>
          {task.sources[0] && <SourceIcon kind={task.sources[0].kind} className="size-3" />}
          {ref && <span className="mono min-w-0 truncate">{ref}</span>}
          {task.source_state && (
            <span title={`Source ${task.source_state}`}>
              <TriangleAlert className="size-3 text-st-approval" />
            </span>
          )}
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            {task.is_demo && <span className="rounded-[3px] bg-raised px-1 text-[10px]">demo</span>}
            <PriorityIcon priority={task.priority} />
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-[13.5px] leading-[19px] font-medium text-text">{task.title}</p>
        {task.labels.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {task.labels.slice(0, 4).map((l) => (
              <span key={l} className="rounded-[4px] border border-line-soft px-1.5 text-2xs leading-4 text-text-2">
                {l}
              </span>
            ))}
          </div>
        )}

        {(run || task.open_dependencies > 0 || task.last_activity) && (
          <div className="mt-2 flex items-center gap-2 border-t border-line-soft pt-1.5 text-2xs text-text-3">
            {run && run.agents.length > 0 && (
              <span className="flex items-center -space-x-0.5" aria-label="Agents">
                {run.agents.slice(0, 5).map((a) => {
                  const st = sessionState(String(a.status));
                  return (
                    <span
                      key={String(a.role)}
                      title={`${a.name}: ${STATE[st].label}`}
                      className={cn("flex h-4 min-w-4 items-center justify-center rounded-[4px] border border-panel px-0.5 text-[9px] font-bold text-bg", st === "running" && "ring-1 ring-st-run")}
                      style={{ background: roleColor(String(a.role)) }}
                    >
                      {String(a.name).slice(0, 1)}
                    </span>
                  );
                })}
              </span>
            )}
            {run && run.subtasks > 0 && (
              <span className="flex items-center gap-0.5" title="Subtasks done">
                <ListTree className="size-3" /> {run.subtasks_done}/{run.subtasks}
              </span>
            )}
            {run && run.pending_approvals > 0 && (
              <span className="flex items-center gap-0.5 text-st-approval">
                <Hand className="size-3" /> {run.pending_approvals}
              </span>
            )}
            {task.open_dependencies > 0 && (
              <span className="flex items-center gap-0.5 text-st-dep" title="Waiting on dependencies">
                <Link2 className="size-3" /> {task.open_dependencies}
              </span>
            )}
            <span className={cn("min-w-0 flex-1 truncate", active && "text-text-2")} title={task.last_activity ?? undefined}>
              {active && run ? STATE[runState(run.status)].label : task.last_activity}
            </span>
            <span className="shrink-0">{relativeTime(task.last_activity_at ?? task.updated_at)}</span>
          </div>
        )}
      </div>
    </article>
  );
});

export function SortableTaskCard({ task, onOpen, disabled }: { task: Task; onOpen: () => void; disabled?: boolean }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: task.id, data: { status: task.status }, disabled });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      {...attributes}
      {...listeners}
      aria-label={`${task.key} ${task.title}. Press Enter to open, Space to move.`}
      onKeyDown={(e) => {
        listeners?.onKeyDown?.(e);
        if (e.key === "Enter" && !isDragging) onOpen();
      }}
      className="rounded-[8px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <TaskCardView task={task} onOpen={onOpen} dragging={isDragging} focusable={false} />
    </div>
  );
}
