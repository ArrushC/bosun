import { Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { useProviderStatus, useRunWorkflow, useSettings, useTasks, useWorkspaces } from "@/api/hooks";
import type { Role, Task } from "@/api/types";
import { Button } from "@/components/ui/button";
import { ErrorNote, KeyValue, Notice, RoleTag, Spinner } from "@/components/ui/display";
import { Field, Input, NativeSelect, SearchInput, Segmented, Textarea } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { RUN_LABEL } from "@/lib/status";
import { cn, newIdempotencyKey, plural, tildePath } from "@/lib/utils";
import type { Step } from "@/lib/workflow";

import { agentColor } from "./parts";

const ACTIVE = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

function runnable(task: Task): string | null {
  if (task.archived) return "Archived";
  if (task.status === "done") return "Done";
  if (!task.workspace_id) return "No workspace";
  if (task.latest_run && ACTIVE.has(task.latest_run.status)) return "Already running";
  return null;
}

export function RunWorkflowDialog({
  open,
  onOpenChange,
  workflowId,
  name,
  revision,
  saving = false,
  steps,
  roles,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  name: string;
  revision: number;
  /** True while the draft has changes the server has not stored yet: the run would start from an older revision. */
  saving?: boolean;
  steps: Step[];
  roles: Role[];
}) {
  const navigate = useNavigate();
  const run = useRunWorkflow(workflowId);
  const { data: tasks = [], isLoading: tasksLoading } = useTasks();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: settings } = useSettings();
  const { data: provider } = useProviderStatus();
  const [target, setTarget] = useState<"new" | "existing">("new");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [query, setQuery] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const key = useRef(newIdempotencyKey());
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    key.current = newIdempotencyKey();
    run.reset();
  }, [open]); // not `run`: it is a new object on every render
  useEffect(() => {
    if (!workspaceId && workspaces.length) setWorkspaceId(workspaces[0].id);
  }, [workspaces, workspaceId]);

  // The error sits below the plan summary; bring it into view so a refused start isn't missed.
  useEffect(() => {
    if (run.error) errorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [run.error]);

  const agents = useMemo(() => {
    const keys = [...new Set(steps.map((s) => s.agent).filter((a): a is string => !!a))];
    return keys.map((k) => roles.find((r) => r.key === k)).filter((r): r is Role => !!r);
  }, [steps, roles]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks
      .filter((t) => !t.archived && t.status !== "done")
      .filter((t) => !q || t.title.toLowerCase().includes(q) || t.key.toLowerCase().includes(q))
      .slice(0, 50);
  }, [tasks, query]);

  const chosen = tasks.find((t) => t.id === taskId);
  const ready = target === "new" ? title.trim().length > 0 && !!workspaceId : !!chosen && !runnable(chosen);
  const parallel = settings?.execution.max_parallel_per_run;

  const start = () => {
    if (!ready || saving || run.isPending) return;
    run.mutate(
      {
        idempotencyKey: key.current,
        body: target === "new" ? { revision, new_task: { title: title.trim(), description: description.trim(), workspace_id: workspaceId } } : { revision, task_id: taskId },
      },
      {
        onSuccess: (r) => {
          onOpenChange(false);
          setTitle("");
          setDescription("");
          setTaskId(null);
          toast.success(`Run ${r.number} started on ${r.task_key}`, {
            description: `${name} is running in its own worktree.`,
            action: { label: "Open run", onClick: () => navigate(`/runs/${r.id}`) },
          });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={`Run ${name}`}
      description="The workflow runs on a board task, in its own worktree. Nothing reaches your branch until you merge it."
      footer={
        <>
          {saving && <span className="mr-auto text-sm text-text-3">Saving your latest changes…</span>}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={start} disabled={!ready || saving || run.isPending}>
            {run.isPending ? <Spinner className="text-accent-fg" /> : <Play />} Start run
          </Button>
        </>
      }
    >
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          start();
        }}
      >
        <Segmented
          value={target}
          onChange={setTarget}
          options={[
            { value: "new", label: "New task" },
            { value: "existing", label: "Existing task" },
          ]}
        />
        {target === "new" ? (
          workspaces.length === 0 ? (
            <Notice>
              Add a workspace first: the run needs a project folder to work in. <Link to="/integrations" className="text-accent-strong hover:underline">Add a workspace</Link>
            </Notice>
          ) : (
            <>
              <Field label="Task title">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} autoFocus placeholder="What should the workflow work on?" />
              </Field>
              <Field label="Description" hint="Every step sees this, so describe the outcome you want.">
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
              </Field>
              <Field label="Workspace">
                <NativeSelect value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)}>
                  {workspaces.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name} ({tildePath(w.path)})
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </>
          )
        ) : (
          <div className="space-y-3">
            <SearchInput value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a task" aria-label="Find a task" autoFocus />
            <div role="radiogroup" aria-label="Task" className="max-h-64 divide-y divide-line-soft overflow-y-auto rounded-[12px] border border-line-soft">
              {tasksLoading && <p className="px-4 py-3 text-sm text-text-3">Loading tasks…</p>}
              {!tasksLoading && matches.length === 0 && <p className="px-4 py-3 text-sm text-text-3">{query ? "No tasks match." : "No open tasks yet. Start with a new task instead."}</p>}
              {matches.map((t) => {
                const reason = runnable(t);
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="radio"
                    aria-checked={taskId === t.id}
                    disabled={!!reason}
                    onClick={() => setTaskId(t.id)}
                    className={cn("flex w-full items-center gap-3 px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-50", taskId === t.id ? "bg-accent-soft" : "hover:bg-raised")}
                  >
                    <span className={cn("size-4 shrink-0 rounded-full border", taskId === t.id ? "border-[5px] border-accent" : "border-line")} />
                    <span className="mono w-16 shrink-0 text-xs text-text-3">{t.key}</span>
                    <span className="min-w-0 flex-1 truncate text-sm">{t.title}</span>
                    <span className="shrink-0 text-xs text-text-3">{reason ?? (t.latest_run ? `Last run ${RUN_LABEL[t.latest_run.status]?.toLowerCase() ?? t.latest_run.status}` : "")}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <section className="rounded-[14px] bg-sunken px-5 py-4">
          <h3 className="mb-3 text-sm font-semibold">What will happen</h3>
          <KeyValue
            items={[
              ["Steps", `${plural(steps.length, "step")}${parallel ? `, up to ${parallel} at a time` : ""}`],
              [
                "Agents",
                <span className="flex flex-wrap gap-1.5">
                  {agents.map((a) => (
                    <RoleTag key={a.key} name={a.name} color={agentColor(a)} />
                  ))}
                </span>,
              ],
              [
                "Billing",
                settings?.execution.backend === "demo"
                  ? "Demo backend: no Claude calls are made"
                  : provider
                    ? provider.ready
                      ? provider.summary
                      : `${provider.summary} (fix this before running)`
                    : "Checking…",
              ],
              ["Approvals", "Shell commands outside the allowed list ask you first"],
            ]}
          />
        </section>
        {run.error && (
          <div ref={errorRef}>
            <ErrorNote title="The run did not start">{(run.error as Error).message}</ErrorNote>
          </div>
        )}
      </form>
    </Dialog>
  );
}
