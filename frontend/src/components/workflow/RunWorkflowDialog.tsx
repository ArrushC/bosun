import { Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { useProviderStatus, useRunWorkflow, useSettings, useTasks, useWorkflowVersion, useWorkflowVersions, useWorkspaces } from "@/api/hooks";
import type { Role, Task } from "@/api/types";
import { Button } from "@/components/ui/button";
import { ErrorNote, KeyValue, Notice, Spinner } from "@/components/ui/display";
import { Field, Input, NativeSelect, SearchInput, Segmented, Textarea } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { RUN_LABEL } from "@/lib/status";
import { cn, newIdempotencyKey, plural, relativeTime, tildePath } from "@/lib/utils";
import { normalizeStep, sourceLabel, type Step } from "@/lib/workflow";

import { AgentChangeNotes, AgentTags } from "./versions";

const ACTIVE = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

function runnable(task: Task): string | null {
  if (task.archived) return "Archived";
  if (task.status === "done") return "Done";
  if (!task.workspace_id) return "No workspace";
  if (task.latest_run && ACTIVE.has(task.latest_run.status)) return "Already running";
  return null;
}

/** What a run executes: the draft, or a published version's number. */
export type RunSource = "draft" | number;

export function RunWorkflowDialog({
  open,
  onOpenChange,
  workflowId,
  name,
  revision,
  saving = false,
  steps,
  roles,
  draftState,
  draftProblem,
  initialSource,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  name: string;
  /** The draft's saved revision; a draft run is refused if the draft changes after this. */
  revision: number;
  /** True while the draft has changes the server has not stored yet: the run would start from an older revision. */
  saving?: boolean;
  /** The draft's steps. */
  steps: Step[];
  roles: Role[];
  /** "unpublished changes", "same as version 3"… shown with the draft option. */
  draftState: string;
  /** Why the draft can't run, if it can't (a version still can). */
  draftProblem?: string | null;
  /** Preselect this source; otherwise the latest version, or the draft if there is none. */
  initialSource?: RunSource;
}) {
  const navigate = useNavigate();
  const run = useRunWorkflow(workflowId);
  const { data: versions = [], isLoading: versionsLoading } = useWorkflowVersions(workflowId);
  const [source, setSource] = useState<RunSource>("draft");
  const chosen = useWorkflowVersion(workflowId, source === "draft" ? null : source);
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
  // Choose what to run once the dialog is open and the versions are known.
  const picked = useRef(false);
  useEffect(() => {
    if (!open) {
      picked.current = false;
      return;
    }
    if (picked.current || versionsLoading) return;
    picked.current = true;
    setSource(initialSource ?? (versions[0]?.number ?? "draft"));
  }, [open, versionsLoading, versions, initialSource]);
  useEffect(() => {
    if (!workspaceId && workspaces.length) setWorkspaceId(workspaces[0].id);
  }, [workspaces, workspaceId]);

  // The error sits below the plan summary; bring it into view so a refused start isn't missed.
  useEffect(() => {
    if (run.error) errorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [run.error]);

  const version = source === "draft" ? null : chosen.data && chosen.data.number === source ? chosen.data : null;
  const runSteps = useMemo(() => (source === "draft" ? steps : (version?.spec.steps ?? []).map(normalizeStep)), [source, steps, version]);
  const agents = useMemo(() => {
    if (version) return version.agents;
    const keys = [...new Set(runSteps.filter((s) => s.kind === "agent").map((s) => s.agent).filter((a): a is string => !!a))];
    return keys.map((k) => roles.find((r) => r.key === k)).filter((r): r is Role => !!r);
  }, [version, runSteps, roles]);
  const approvals = runSteps.filter((s) => s.kind === "approval").map((s) => s.title.trim() || "Untitled step");
  const versionProblem = version?.issues[0]?.message ?? null;
  const sourceProblem = source === "draft" ? (draftProblem ?? null) : chosen.error ? (chosen.error as Error).message : versionProblem;
  const sourceLoading = source !== "draft" && !version && !chosen.error;
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks
      .filter((t) => !t.archived && t.status !== "done")
      .filter((t) => !q || t.title.toLowerCase().includes(q) || t.key.toLowerCase().includes(q))
      .slice(0, 50);
  }, [tasks, query]);

  const chosenTask = tasks.find((t) => t.id === taskId);
  const ready = target === "new" ? title.trim().length > 0 && !!workspaceId : !!chosenTask && !runnable(chosenTask);
  const parallel = settings?.execution.max_parallel_per_run;
  const waitingOnDraft = source === "draft" && saving;
  const canStart = ready && !waitingOnDraft && !sourceProblem && !sourceLoading && !run.isPending;

  const start = () => {
    if (!canStart) return;
    const what = { revision, ...(source === "draft" ? {} : { version: source }) };
    run.mutate(
      {
        idempotencyKey: key.current,
        body: target === "new" ? { ...what, new_task: { title: title.trim(), description: description.trim(), workspace_id: workspaceId } } : { ...what, task_id: taskId },
      },
      {
        onSuccess: (r) => {
          onOpenChange(false);
          setTitle("");
          setDescription("");
          setTaskId(null);
          toast.success(`Run ${r.number} started on ${r.task_key}`, {
            description: `${name}, ${sourceLabel(source === "draft" ? null : source)}, is running in its own worktree.`,
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
          {waitingOnDraft && <span className="mr-auto text-sm text-text-3">Saving your latest changes…</span>}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={start} disabled={!canStart}>
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
        <Field label="What to run" hint={source === "draft" ? "The draft as it is now. Publish it to keep a fixed copy." : "A published version: fixed steps, with its agents as they were when it was published."}>
          <NativeSelect value={String(source)} onChange={(e) => setSource(e.target.value === "draft" ? "draft" : Number(e.target.value))} disabled={versionsLoading}>
            {versions.map((v, i) => (
              <option key={v.number} value={v.number}>
                Version {v.number}
                {i === 0 ? " (latest)" : ""}
                {v.note ? `: ${v.note.length > 60 ? `${v.note.slice(0, 60)}…` : v.note}` : ""}
              </option>
            ))}
            <option value="draft" disabled={!!draftProblem}>
              Draft ({draftProblem ? "has problems" : draftState})
            </option>
          </NativeSelect>
        </Field>
        <section className="rounded-[14px] bg-sunken px-5 py-4">
          <h3 className="mb-3 text-sm font-semibold">What will happen</h3>
          <KeyValue
            items={[
              [
                "Runs",
                version ? `Version ${version.number}, published ${relativeTime(version.created_at)}` : source === "draft" ? "The draft as it is now" : sourceLoading ? "Loading…" : "—",
              ],
              ["Steps", `${plural(runSteps.length, "step")}${parallel ? `, up to ${parallel} agents at a time` : ""}`],
              ["Agents", <AgentTags agents={agents} />],
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
              [
                "Approvals",
                approvals.length
                  ? `The run waits for you at ${approvals.join(", ")}. Shell commands outside the allowed list also ask you first.`
                  : "Shell commands outside the allowed list ask you first",
              ],
            ]}
          />
        </section>
        {version && <AgentChangeNotes changes={version.agent_changes} version={version.number} />}
        {sourceProblem && <ErrorNote title={source === "draft" ? "The draft can't run yet" : `Version ${source} can't run now`}>{sourceProblem}</ErrorNote>}
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
        {run.error && (
          <div ref={errorRef}>
            <ErrorNote title="The run did not start">{(run.error as Error).message}</ErrorNote>
          </div>
        )}
      </form>
    </Dialog>
  );
}
