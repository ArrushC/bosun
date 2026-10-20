import {
  Archive,
  ArrowUpRight,
  ExternalLink,
  GitBranch,
  Link2,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useArchiveTask, useMoveTask, useTask, useTaskAction, useTasks } from "@/api/hooks";
import type { RunBrief, TaskDetail, TaskStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  KeyValue,
  Md,
  PriorityIcon,
  RoleTag,
  SourceIcon,
  Spinner,
  StatusPill,
  TabPanel,
  Tabs,
} from "@/components/ui/display";
import { Input, NativeSelect } from "@/components/ui/inputs";
import { DropdownContent, DropdownItem, DropdownMenu, DropdownSeparator, DropdownTrigger, Sheet, Tip } from "@/components/ui/overlays";
import { KIND_LABEL, PRIORITY, roleColor, RUN_LABEL, runState, TASK_COLUMNS } from "@/lib/status";
import { cn, relativeTime } from "@/lib/utils";

import { LaunchDialog } from "./LaunchDialog";
import { TaskFormDialog } from "./TaskFormDialog";

const ACTIVE = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

function SourceList({ task }: { task: TaskDetail }) {
  if (!task.sources.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">Sources</h3>
      <ul className="space-y-2">
        {task.sources.map((s) => (
          <li key={s.id} className="flex items-center gap-2 text-sm">
            <SourceIcon kind={s.kind} />
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer noopener" className="flex min-w-0 items-center gap-1.5 hover:text-accent-strong">
                <span className="mono shrink-0 text-text-2">
                  {s.repo}#{s.number}
                </span>
                <span className="truncate">{s.title}</span>
                <ExternalLink className="size-3.5 shrink-0 text-text-3" />
              </a>
            ) : (
              <span className="mono truncate text-text-2">
                {s.path}:{s.line}
              </span>
            )}
            {s.state !== "open" && <span className="ml-auto shrink-0 rounded-full bg-raised px-2 text-xs text-text-2">{s.state}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Dependencies({ task }: { task: TaskDetail }) {
  const { data: tasks = [] } = useTasks();
  const add = useTaskAction<{ depends_on_id: string }>(task.id, "/dependencies");
  const [adding, setAdding] = useState(false);
  const candidates = tasks.filter((t) => t.id !== task.id && !task.depends_on.includes(t.id));
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Dependencies</h3>
        <Button size="xs" variant="ghost" onClick={() => setAdding((v) => !v)}>
          <Plus /> Add
        </Button>
      </div>
      {adding && (
        <NativeSelect
          autoFocus
          value=""
          onChange={(e) => {
            if (e.target.value) add.mutate({ depends_on_id: e.target.value });
            setAdding(false);
          }}
        >
          <option value="">This task depends on…</option>
          {candidates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.key} {t.title}
            </option>
          ))}
        </NativeSelect>
      )}
      {!task.dependencies.length && !task.dependents.length && !adding && <p className="text-sm text-text-3">No dependencies.</p>}
      <ul className="space-y-2">
        {task.dependencies.map((d) => (
          <DependencyRow key={d.id} taskId={task.id} dep={d} direction="blocked by" removable />
        ))}
        {task.dependents.map((d) => (
          <DependencyRow key={d.id} taskId={task.id} dep={d} direction="blocks" />
        ))}
      </ul>
    </section>
  );
}

function DependencyRow({ taskId, dep, direction, removable }: { taskId: string; dep: TaskDetail["dependencies"][number]; direction: string; removable?: boolean }) {
  const remove = useTaskAction<void>(taskId, `/dependencies/${dep.id}`, "delete");
  return (
    <li className="group flex items-center gap-2.5 text-sm">
      <Link2 className={cn("size-4", dep.status === "done" ? "text-st-ok" : "text-st-dep")} />
      <span className="w-20 shrink-0 text-xs text-text-3">{direction}</span>
      <Link to={`?task=${dep.id}`} className="mono shrink-0 text-text-2 hover:text-text">
        {dep.key}
      </Link>
      <span className="truncate">{dep.title}</span>
      <span className="ml-auto shrink-0 text-xs text-text-3">{TASK_COLUMNS.find((c) => c.id === dep.status)?.label}</span>
      {removable && (
        <button aria-label="Remove dependency" className="opacity-0 group-hover:opacity-100" onClick={() => remove.mutate()}>
          <X className="size-3.5 text-text-3 hover:text-text" />
        </button>
      )}
    </li>
  );
}

function RunRow({ run }: { run: RunBrief }) {
  return (
    <Link
      to={`/runs/${run.id}`}
      className="flex items-center gap-4 rounded-[14px] border border-line-soft px-4 py-3.5 transition-colors hover:border-line hover:bg-raised"
    >
      <StatusPill state={runState(run.status)} label={RUN_LABEL[run.status]} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          Run {run.number}
          <span className="ml-2 text-xs font-normal text-text-3">
            {run.mode === "single" ? "Single agent" : run.strategy === "single" ? "Auto: single agent" : `Team of ${run.sessions}`}
            {run.backend === "demo" ? ", demo" : ""}
          </span>
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {run.agents.map((a) => (
            <RoleTag key={String(a.role)} name={`${a.name}${Number(a.count) > 1 ? ` ×${a.count}` : ""}`} color={roleColor(String(a.role))} />
          ))}
        </div>
      </div>
      <div className="text-right text-xs text-text-3">
        <p>{relativeTime(run.started_at)}</p>
        {run.pending_approvals > 0 && <p className="text-st-approval">{run.pending_approvals} waiting</p>}
      </div>
      <ArrowUpRight className="size-4 text-text-3" />
    </Link>
  );
}

function Activity({ task }: { task: TaskDetail }) {
  const comment = useTaskAction<{ message: string }>(task.id, "/comments");
  const [text, setText] = useState("");
  return (
    <div className="space-y-6 px-8 py-6">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) comment.mutate({ message: text.trim() }, { onSuccess: () => setText("") });
        }}
      >
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a note" aria-label="Add a note" />
        <Button type="submit" variant="secondary" disabled={!text.trim()}>
          <MessageSquare /> Note
        </Button>
      </form>
      <ol className="relative space-y-5 border-l border-line-soft pl-5">
        {task.activity.map((a) => (
          <li key={a.id} className="relative">
            <span
              className={cn(
                "absolute top-2 -left-[25px] size-2.5 rounded-full border-2 border-panel",
                a.actor === "system" ? "bg-st-dep" : a.kind === "comment" ? "bg-accent" : "bg-text-3",
              )}
            />
            <p className={cn("text-sm", a.kind === "comment" ? "text-text" : "text-text-2")}>{a.message}</p>
            <p className="mt-0.5 text-xs text-text-3">
              {a.actor === "system" ? "Bosun" : "You"}, {relativeTime(a.created_at)}
              {a.run_id && (
                <Link to={`/runs/${a.run_id}`} className="ml-2 hover:text-text-2">
                  view run
                </Link>
              )}
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function TaskSheet({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const { data: task, isLoading, error } = useTask(taskId);
  const move = useMoveTask();
  const archive = useArchiveTask();
  const [tab, setTab] = useState("overview");
  const [editing, setEditing] = useState(false);
  const [launching, setLaunching] = useState(false);
  useEffect(() => {
    setTab("overview");
    setEditing(false);
    setLaunching(false);
  }, [taskId]);

  const activeRun = task?.runs.find((r) => ACTIVE.has(r.status));

  return (
    <Sheet open={!!taskId} onOpenChange={(open) => !open && onClose()} label={task ? `${task.key} ${task.title}` : "Task"} width={720}>
      {isLoading && (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      )}
      {error && <EmptyState title="This task could not be loaded" icon={TriangleAlert}>{(error as Error).message}</EmptyState>}
      {task && (
        <>
          <header className="shrink-0 px-8 pt-6 pb-6">
            <div className="flex items-center gap-2">
              <span className="mono text-sm text-text-3">{task.key}</span>
              {task.is_demo && <span className="rounded-full bg-raised px-2 text-xs text-text-2">demo</span>}
              <div className="ml-auto flex items-center gap-1">
                <Tip label="Edit task">
                  <Button size="icon-sm" variant="ghost" aria-label="Edit task" onClick={() => setEditing(true)}>
                    <Pencil />
                  </Button>
                </Tip>
                <DropdownMenu>
                  <DropdownTrigger asChild>
                    <Button size="icon-sm" variant="ghost" aria-label="More actions">
                      <MoreHorizontal />
                    </Button>
                  </DropdownTrigger>
                  <DropdownContent>
                    <DropdownItem onSelect={() => setEditing(true)}>
                      <Pencil /> Edit
                    </DropdownItem>
                    <DropdownSeparator />
                    <DropdownItem
                      danger
                      onSelect={() => {
                        if (window.confirm(`Archive ${task.key}? It disappears from the board; its runs and history are kept.`)) {
                          archive.mutate(task.id, { onSuccess: onClose });
                        }
                      }}
                    >
                      <Archive /> Archive task
                    </DropdownItem>
                  </DropdownContent>
                </DropdownMenu>
                <Button size="icon-sm" variant="ghost" aria-label="Close" onClick={onClose}>
                  <X />
                </Button>
              </div>
            </div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight">{task.title}</h2>
            {task.source_state && (
              <p className="mt-2 flex items-center gap-2 text-sm text-st-approval">
                <TriangleAlert className="size-4" /> The source of this task was {task.source_state}.
              </p>
            )}
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <NativeSelect
                aria-label="Column"
                value={task.status}
                onChange={(e) => move.mutate({ id: task.id, status: e.target.value as TaskStatus })}
                className="w-40"
              >
                {TASK_COLUMNS.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </NativeSelect>
              {activeRun ? (
                <Button variant="secondary" asChild>
                  <Link to={`/runs/${activeRun.id}`}>
                    <StatusPill state={runState(activeRun.status)} compact /> Watch run {activeRun.number}
                  </Link>
                </Button>
              ) : (
                <Button variant="primary" onClick={() => setLaunching(true)} disabled={!task.workspace_id}>
                  <Sparkles /> Run with agent
                </Button>
              )}
              {!task.workspace_id && <span className="text-sm text-text-3">Assign a workspace to run agents.</span>}
            </div>
          </header>

          <Tabs
            value={tab}
            onValueChange={setTab}
            className="min-h-0 flex-1"
            tabs={[
              { value: "overview", label: "Overview" },
              { value: "runs", label: "Runs", count: task.runs.length },
              { value: "activity", label: "Activity", count: task.activity.length },
            ]}
          >
            <TabPanel value="overview" className="space-y-8 px-8 py-7">
              {task.description ? <Md>{task.description}</Md> : <p className="text-sm text-text-3">No description.</p>}
              {task.acceptance_criteria && (
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">Acceptance criteria</h3>
                  <Md>{task.acceptance_criteria}</Md>
                </section>
              )}
              {task.agent_instructions && (
                <section className="space-y-2">
                  <h3 className="text-sm font-semibold">Instructions for agents</h3>
                  <Md>{task.agent_instructions}</Md>
                </section>
              )}
              <SourceList task={task} />
              <Dependencies task={task} />
              {task.attachments.length > 0 && (
                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">Files and links</h3>
                  <ul className="space-y-1.5">
                    {task.attachments.map((a, i) => (
                      <li key={i} className="mono truncate text-sm text-text-2">
                        {String(a.type) === "url" ? (
                          <a href={String(a.value)} target="_blank" rel="noreferrer noopener" className="hover:text-accent-strong">
                            {String(a.value)}
                          </a>
                        ) : (
                          String(a.value)
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <section className="border-t border-line-soft pt-6">
                <KeyValue
                  items={[
                    ["Workspace", task.workspace_name ?? <span className="text-text-3">None</span>],
                    ["Type", KIND_LABEL[task.kind] ?? task.kind],
                    [
                      "Priority",
                      <span className="inline-flex items-center gap-1.5">
                        <PriorityIcon priority={task.priority} /> {PRIORITY[task.priority]?.label}
                      </span>,
                    ],
                    ["Execution", task.execution_mode === "single" ? "Single agent" : task.execution_mode === "team" ? "Agent team" : "Coordinator decides"],
                    ["Labels", task.labels.length ? task.labels.join(", ") : <span className="text-text-3">None</span>],
                    ["Created", relativeTime(task.created_at)],
                    ["Updated", relativeTime(task.updated_at)],
                  ]}
                />
              </section>
            </TabPanel>
            <TabPanel value="runs" className="space-y-3 px-8 py-6">
              {task.runs.length ? (
                task.runs.map((r) => <RunRow key={r.id} run={r} />)
              ) : (
                <EmptyState icon={GitBranch} title="No runs yet" action={<Button variant="primary" onClick={() => setLaunching(true)} disabled={!task.workspace_id}><Sparkles /> Run with agent</Button>}>
                  Each run works in its own git worktree, so your checkout stays untouched until you merge.
                </EmptyState>
              )}
            </TabPanel>
            <TabPanel value="activity">
              <Activity task={task} />
            </TabPanel>
          </Tabs>
          <TaskFormDialog key={`edit-${task.id}`} open={editing} onOpenChange={setEditing} task={task} />
          <LaunchDialog key={`launch-${task.id}`} task={task} open={launching} onOpenChange={setLaunching} />
        </>
      )}
    </Sheet>
  );
}

