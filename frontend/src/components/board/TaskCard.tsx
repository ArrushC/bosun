import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Link2, ListTree, TriangleAlert } from "lucide-react";
import { memo } from "react";

import type { Task } from "@/api/types";
import { PriorityIcon } from "@/components/ui/display";
import { roleColor, runState, sessionState, STATE, type VisualState } from "@/lib/status";
import { cn } from "@/lib/utils";

const ACTIVE_RUN = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

/** The card's left edge carries the agent state — the flight-strip colour band. */
function edgeState(task: Task): VisualState | null {
  const run = task.latest_run;
  if (run && (run.pending_approvals > 0 || run.status === "waiting_approval")) return "waiting_approval";
  if (run && ACTIVE_RUN.has(run.status)) return "running";
  if (task.open_dependencies > 0 && task.status !== "done") return "waiting_dependency";
  if (run?.status === "failed") return "failed";
  if (run?.status === "paused" || run?.status === "interrupted") return "paused";
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
  // Only work an agent is on (or that is blocked) gets a status line; idle cards stay quiet.
  const state = edge === "running" && run ? runState(run.status) : edge;

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
        "group relative flex cursor-pointer overflow-hidden rounded-[14px] border border-line-soft bg-panel transition-[border-color,box-shadow,background-color] select-none hover:border-line focus-visible:border-accent",
        dragging && "opacity-30",
        overlay && "rotate-[0.6deg] border-line shadow-pop",
      )}
    >
      {edge && (
        <span
          aria-hidden
          className={cn(
            "w-1 shrink-0",
            edge === "running" && "state-edge-running",
            edge === "waiting_dependency" && "state-edge-dependency",
            edge === "waiting_approval" && "animate-pulse-ring",
          )}
          style={edge !== "running" && edge !== "waiting_dependency" ? { background: STATE[edge].color } : undefined}
        />
      )}
      <div className="min-w-0 flex-1 px-4 py-3.5">
        <div className="flex items-start gap-3">
          <p className="line-clamp-3 min-w-0 flex-1 text-base leading-6 font-medium text-text">{task.title}</p>
          {task.priority !== "none" && <PriorityIcon priority={task.priority} className="mt-1 shrink-0" />}
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-xs text-text-3">
          <span className="mono shrink-0">{task.key}</span>
          {ref && <span className="mono min-w-0 shrink truncate">{ref}</span>}
          {task.source_state && (
            <span title={`Source ${task.source_state}`}>
              <TriangleAlert className="size-3.5 text-st-approval" />
            </span>
          )}
          {task.labels.length > 0 && <span className="min-w-0 truncate">{task.labels.slice(0, 3).join(", ")}</span>}
          {task.is_demo && <span className="ml-auto shrink-0">demo</span>}
        </div>

        {(state || task.open_dependencies > 0) && (
          <div className="mt-3 flex items-center gap-3 text-xs text-text-2">
            {state && (
              <span className="flex min-w-0 items-center gap-1.5 font-medium" style={{ color: STATE[state].color }}>
                {(() => {
                  const Icon = STATE[state].icon;
                  return <Icon className={cn("size-3.5 shrink-0", STATE[state].spin && "animate-spin [animation-duration:1.6s]")} />;
                })()}
                <span className="truncate">{STATE[state].label}</span>
              </span>
            )}
            {run && run.subtasks > 0 && (
              <span className="flex items-center gap-1" title="Subtasks done">
                <ListTree className="size-3.5" /> {run.subtasks_done}/{run.subtasks}
              </span>
            )}
            {task.open_dependencies > 0 && (
              <span className="flex items-center gap-1 text-st-dep" title="Waiting on dependencies">
                <Link2 className="size-3.5" /> {task.open_dependencies}
              </span>
            )}
            {run && run.agents.length > 0 && (
              <span className="ml-auto flex items-center -space-x-1" aria-label="Agents">
                {run.agents.slice(0, 4).map((a) => {
                  const st = sessionState(String(a.status));
                  return (
                    <span
                      key={String(a.role)}
                      title={`${a.name}: ${STATE[st].label}`}
                      className={cn("flex size-5 items-center justify-center rounded-full border-2 border-panel text-[10px] font-bold text-bg", st === "running" && "ring-1 ring-st-run")}
                      style={{ background: roleColor(String(a.role)) }}
                    >
                      {String(a.name).slice(0, 1)}
                    </span>
                  );
                })}
              </span>
            )}
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
      className="rounded-[14px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <TaskCardView task={task} onOpen={onOpen} dragging={isDragging} focusable={false} />
    </div>
  );
}
