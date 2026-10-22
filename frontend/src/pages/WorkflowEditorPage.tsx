import { ArrowLeft, Check, ChevronRight, Ellipsis, History, Play, Plus, Redo2, RefreshCw, Trash2, TriangleAlert, Undo2, Upload, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useDeleteWorkflow, useRoles, useRuns, useWorkflowVersion, useWorkflowVersions } from "@/api/hooks";
import type { Role, WorkflowIssue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Kbd, StatusPill, TabPanel, Tabs } from "@/components/ui/display";
import { DropdownContent, DropdownItem, DropdownMenu, DropdownSeparator, DropdownTrigger, Tip } from "@/components/ui/overlays";
import { type RunSource, RunWorkflowDialog } from "@/components/workflow/RunWorkflowDialog";
import { StepInspector, WorkflowDetails } from "@/components/workflow/StepInspector";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { AgentMenu, countIssues, IssueIcon, SaveStatus, stepAgents } from "@/components/workflow/parts";
import { useWorkflowDraft } from "@/components/workflow/useWorkflowDraft";
import { DraftBadge, draftState, PublishDialog, VersionList, versionContent } from "@/components/workflow/versions";
import { RUN_LABEL, runState } from "@/lib/status";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { cn, elapsed, formatDuration, plural, relativeTime } from "@/lib/utils";
import { addStep, connect, disconnect, MAX_STEPS, removeSteps, specChanges, type Step, stepTitle, updateStep } from "@/lib/workflow";

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
}

export function WorkflowEditorPage() {
  const { workflowId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const editor = useWorkflowDraft(workflowId);
  const { draft } = editor;
  // An empty canvas already asks for the first step, so that isn't listed as a problem.
  const issues = useMemo(() => (draft?.steps.length ? editor.issues : editor.issues.filter((i) => i.code !== "no_steps")), [draft, editor.issues]);
  const { data: roles = [] } = useRoles();
  const agents = useMemo(() => stepAgents(roles), [roles]);
  const { data: runs } = useRuns({ workflow_id: workflowId });
  const { data: versions } = useWorkflowVersions(workflowId);
  const server = editor.query.data;
  const latest = useWorkflowVersion(workflowId, server?.latest_version?.number ?? null);
  // Compared here rather than by the server, so the draft's state follows each edit at once.
  const changes = useMemo(() => (latest.data && draft ? specChanges(versionContent(latest.data), draft) : null), [latest.data, draft]);
  const remove = useDeleteWorkflow();
  const narrow = useMediaQuery("(max-width: 1180px)");
  const [selected, setSelected] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [problemsOpen, setProblemsOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [runSource, setRunSource] = useState<RunSource | undefined>(undefined);
  const [publishOpen, setPublishOpen] = useState(false);
  // A step just added or duplicated, whose name gets focus when its settings open.
  const [fresh, setFresh] = useState<string | null>(null);
  const [isNew] = useState(() => params.get("new") === "1");
  const tab = params.get("tab") ?? "editor";
  const setTab = (v: string) => {
    const next = new URLSearchParams(params);
    if (v === "editor") next.delete("tab");
    else next.set("tab", v);
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if (params.has("new")) {
      const next = new URLSearchParams(params);
      next.delete("new");
      setParams(next, { replace: true });
    }
  }, [params, setParams]);

  const steps = draft?.steps ?? [];
  const selectedStep = steps.find((s) => s.key === selected) ?? null;
  const errors = issues.filter((i) => i.level === "error");
  const { undo, change } = editor;

  const select = useCallback((key: string | null) => {
    setSelected(key);
    setFresh((f) => (f === key ? f : null));
    if (key) setPanelOpen(true);
  }, []);

  const editSteps = useCallback(
    (update: (s: Step[]) => Step[], label?: string) => {
      change((d) => ({ ...d, steps: update(d.steps) }));
      if (label?.startsWith("Deleted")) {
        setSelected(null);
        toast(label, { action: { label: "Undo", onClick: () => undo() } });
      }
    },
    [change, undo],
  );

  const add = useCallback(
    (agent: Role, after?: string | null) => {
      if (steps.length >= MAX_STEPS) {
        toast.error(`A workflow can have up to ${MAX_STEPS} steps.`);
        return;
      }
      const result = addStep(steps, { agent: agent.key, title: agent.name, changes_code: agent.permission_profile !== "read_only", after });
      change((d) => ({ ...d, steps: result.steps }));
      select(result.key);
      setFresh(result.key);
    },
    [steps, change, select],
  );

  const addApproval = useCallback(
    (after?: string | null) => {
      if (steps.length >= MAX_STEPS) {
        toast.error(`A workflow can have up to ${MAX_STEPS} steps.`);
        return;
      }
      const result = addStep(steps, { kind: "approval", agent: null, title: "Approve", changes_code: false, after });
      change((d) => ({ ...d, steps: result.steps }));
      select(result.key);
      setFresh(result.key);
    },
    [steps, change, select],
  );

  const duplicate = () => {
    if (!selectedStep) return;
    const copy = addStep(steps, { agent: selectedStep.agent, title: `${selectedStep.title} (copy)`, changes_code: selectedStep.changes_code });
    const { key: _key, after: _after, position: _position, title: _title, ...fields } = selectedStep;
    change((d) => ({
      ...d,
      steps: updateStep(copy.steps, copy.key, { ...fields, after: selectedStep.after, position: { x: selectedStep.position.x + 280, y: selectedStep.position.y } }),
    }));
    select(copy.key);
    setFresh(copy.key);
  };

  const deleteSelected = () => {
    if (!selectedStep) return;
    editSteps((s) => removeSteps(s, [selectedStep.key]), `Deleted ${selectedStep.title || "a step"}`);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // defaultPrevented: a menu or dialog already used the key (Escape closing a menu).
      if (e.defaultPrevented || tab !== "editor" || typing(e.target) || runOpen) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) editor.redo();
        else editor.undo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        editor.redo();
      } else if (!mod && !e.altKey && e.key.toLowerCase() === "a") {
        e.preventDefault();
        setAddOpen(true);
      } else if (e.key === "Escape" && selected) {
        setSelected(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, runOpen, selected, editor]);

  if (editor.query.isLoading || (!draft && !editor.query.error)) return <EditorSkeleton />;
  if (editor.query.error || !draft) {
    const missing = editor.query.error instanceof ApiError && editor.query.error.status === 404;
    return missing ? (
      <EmptyState icon={TriangleAlert} title="This workflow doesn't exist" action={<Button asChild variant="secondary"><Link to="/workflows">All workflows</Link></Button>}>
        It may have been deleted.
      </EmptyState>
    ) : (
      <div className="mx-auto max-w-xl px-8 py-16">
        <ErrorNote
          title="This workflow could not be loaded"
          action={
            <Button size="sm" variant="secondary" onClick={() => void editor.query.refetch()}>
              <RefreshCw /> Try again
            </Button>
          }
        >
          {(editor.query.error as Error).message}. Nothing was lost: the last saved draft is kept on the server.
        </ErrorNote>
      </div>
    );
  }

  const runBlocked =
    editor.saveState === "conflict" ? "Resolve the save conflict first" : !steps.length ? "Add a step to run this workflow" : errors.length ? `Fix ${plural(errors.length, "problem")} first` : null;
  const published = server?.latest_version ?? null;
  const state = draftState(server ?? { latest_version: null, unpublished_changes: true }, changes);
  const agentsChanged = (latest.data?.agent_changes ?? []).some((c) => c.state === "changed");
  const publishBlocked =
    runBlocked ?? (published && !state.changed && !agentsChanged ? `The draft is the same as version ${published.number}` : null);
  // With a published version there is always something to run, even while the draft has problems.
  const runDisabled = published ? null : runBlocked;
  const openRun = (source?: RunSource) => {
    setRunSource(source);
    setRunOpen(true);
  };
  const showPanel = !narrow || panelOpen;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-8 pt-6 pb-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <Link to="/workflows" className="-ml-2 rounded-control p-2 text-text-3 hover:bg-raised hover:text-text" aria-label="All workflows">
            <ArrowLeft className="size-5" />
          </Link>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <h1 className="truncate text-lg font-semibold tracking-tight">{draft.name.trim() || "Untitled workflow"}</h1>
              <SaveStatus state={editor.saveState} onRetry={editor.retryNow} />
              {server && <DraftBadge workflow={server} changes={changes} onClick={() => setTab("versions")} />}
            </div>
            {draft.description && <p className="mt-0.5 truncate text-sm text-text-2">{draft.description}</p>}
          </div>
          {issues.length > 0 ? (
            <Button variant="outline" size="sm" className={errors.length ? "text-st-fail" : "text-st-approval"} onClick={() => setProblemsOpen((v) => !v)} aria-expanded={problemsOpen}>
              <IssueIcon level={errors.length ? "error" : "warning"} /> {countIssues(issues)}
            </Button>
          ) : (
            !runBlocked && (
              <span className="inline-flex items-center gap-1.5 text-sm text-text-3">
                <Check className="size-4" /> Ready to run
              </span>
            )
          )}
          {runBlocked && !errors.length && <span className="text-sm text-text-3">{runBlocked}</span>}
          <Tip label={publishBlocked ?? "Publish the draft as a new version"}>
            <span tabIndex={publishBlocked ? 0 : -1}>
              <Button variant="secondary" disabled={!!publishBlocked} onClick={() => setPublishOpen(true)}>
                <Upload /> Publish
              </Button>
            </span>
          </Tip>
          <Tip label={runDisabled ?? (published ? "Run a version or the draft on a task" : "Run this workflow on a task")}>
            <span tabIndex={runDisabled ? 0 : -1}>
              <Button variant="primary" disabled={!!runDisabled} onClick={() => openRun()}>
                <Play /> Run
              </Button>
            </span>
          </Tip>
          <DropdownMenu>
            <DropdownTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="More actions">
                <Ellipsis className="size-5" />
              </Button>
            </DropdownTrigger>
            <DropdownContent>
              <DropdownItem onSelect={editor.undo} disabled={!editor.canUndo}>
                <Undo2 /> Undo <span className="ml-auto pl-6"><Kbd>Ctrl Z</Kbd></span>
              </DropdownItem>
              <DropdownItem onSelect={editor.redo} disabled={!editor.canRedo}>
                <Redo2 /> Redo <span className="ml-auto pl-6"><Kbd>Ctrl ⇧ Z</Kbd></span>
              </DropdownItem>
              <DropdownSeparator />
              <DropdownItem
                danger
                onSelect={() => {
                  if (!confirm(`Delete ${draft.name || "this workflow"}? Its runs and their results stay.`)) return;
                  remove.mutate(workflowId, {
                    onSuccess: () => {
                      toast(`Deleted ${draft.name || "the workflow"}`);
                      navigate("/workflows");
                    },
                  });
                }}
              >
                <Trash2 /> Delete workflow
              </DropdownItem>
            </DropdownContent>
          </DropdownMenu>
        </div>
        {editor.saveState === "conflict" && (
          <div className="mt-4">
            <ErrorNote
              title="This workflow was changed in another tab or window"
              action={
                <span className="flex shrink-0 gap-2">
                  <Button size="sm" variant="secondary" onClick={() => void editor.reload()}>
                    Load theirs
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void editor.overwrite()}>
                    Keep mine
                  </Button>
                </span>
              }
            >
              Your latest changes are not saved. Load the other version (your changes here are dropped), or keep yours and save over it.
            </ErrorNote>
          </div>
        )}
      </header>

      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "editor", label: "Editor" },
          { value: "versions", label: "Versions", count: versions?.length },
          { value: "runs", label: "Runs", count: runs?.length },
        ]}
        className="min-h-0 flex-1"
      >
        <TabPanel value="editor" className="relative flex overflow-hidden">
          <div className="flex min-w-0 flex-1 flex-col">
            <section aria-label="Workflow steps" className="relative min-h-0 flex-1">
              <WorkflowCanvas
                steps={steps}
                agents={agents}
                allRoles={roles}
                issues={issues}
                selected={selected}
                onSelect={select}
                onChange={editSteps}
                onAdd={add}
                onAddApproval={addApproval}
                addMenuOpen={addOpen}
                onAddMenuOpenChange={setAddOpen}
                panelHidden={!showPanel}
                coveredRight={narrow && showPanel ? 384 : 0}
                onShowPanel={() => setPanelOpen(true)}
                emptyState={<FirstStep agents={agents} onPick={(a) => add(a)} />}
              />
            </section>
            {problemsOpen && issues.length > 0 && (
              <ProblemsPanel
                issues={issues}
                titleOf={(key) => stepTitle(steps, key)}
                onPick={(key) => select(key)}
                onHide={() => setProblemsOpen(false)}
              />
            )}
          </div>
          {showPanel &&
            (selectedStep ? (
              <StepInspector
                key={selectedStep.key}
                step={selectedStep}
                steps={steps}
                roles={roles}
                agents={agents}
                issues={issues}
                overlay={narrow}
                onClose={() => setPanelOpen(false)}
                onCheckpoint={editor.checkpoint}
                focusName={fresh === selectedStep.key}
                onEdit={(patch, opts) => change((d) => ({ ...d, steps: updateStep(d.steps, selectedStep.key, patch) }), opts)}
                onRemoveAfter={(from) => change((d) => ({ ...d, steps: disconnect(d.steps, from, selectedStep.key) }))}
                onWaitFor={(from) => {
                  const next = connect(steps, from, selectedStep.key);
                  if (typeof next === "string") toast.error(next);
                  else change((d) => ({ ...d, steps: next }));
                }}
                onAddAfter={(agent) => add(agent, selectedStep.key)}
                onAddApprovalAfter={() => addApproval(selectedStep.key)}
                onDuplicate={duplicate}
                onDelete={deleteSelected}
              />
            ) : (
              <WorkflowDetails
                name={draft.name}
                description={draft.description}
                stepCount={steps.length}
                issues={issues}
                overlay={narrow}
                onClose={() => setPanelOpen(false)}
                autoFocus={isNew}
                onCheckpoint={editor.checkpoint}
                onEdit={(patch) => change((d) => ({ ...d, ...patch }), { history: false })}
              />
            ))}
        </TabPanel>
        <TabPanel value="versions">
          {server && (
            <VersionList
              workflowId={workflowId}
              workflow={server}
              changes={changes}
              publishBlocked={publishBlocked}
              onEditDraft={() => setTab("editor")}
              onPublish={() => setPublishOpen(true)}
              onRun={(n) => openRun(n)}
            />
          )}
        </TabPanel>
        <TabPanel value="runs">
          <WorkflowRuns workflowId={workflowId} onRun={runDisabled ? undefined : () => openRun()} />
        </TabPanel>
      </Tabs>

      <RunWorkflowDialog
        open={runOpen}
        onOpenChange={setRunOpen}
        workflowId={workflowId}
        name={draft.name || "Untitled workflow"}
        revision={editor.revision}
        saving={editor.saveState !== "saved"}
        steps={steps}
        roles={roles}
        draftState={published ? state.label.toLowerCase() : "not published"}
        draftProblem={runBlocked}
        initialSource={runSource}
      />
      <PublishDialog
        open={publishOpen}
        onOpenChange={setPublishOpen}
        workflowId={workflowId}
        revision={editor.revision}
        draft={draft}
        latest={latest.data ?? null}
        roles={roles}
        errors={errors}
        saving={editor.saveState !== "saved"}
      />
    </div>
  );
}

function FirstStep({ agents, onPick }: { agents: Role[]; onPick: (agent: Role) => void }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-3 rounded-panel bg-panel px-8 py-8 text-center shadow-sm">
        <Workflow className="size-8 text-text-3" strokeWidth={1.4} />
        <p className="text-md font-semibold">Add the first step</p>
        <p className="text-sm leading-6 text-text-2">
          Each step is one agent with its own instructions. Connect steps to set the order; steps with nothing between them run side by side.
        </p>
        <AgentMenu agents={agents} onPick={onPick} align="center">
          <Button variant="primary" className="mt-2">
            <Plus /> Add an agent step
          </Button>
        </AgentMenu>
      </div>
    </div>
  );
}

function ProblemsPanel({ issues, titleOf, onPick, onHide }: { issues: WorkflowIssue[]; titleOf: (key: string) => string; onPick: (key: string) => void; onHide: () => void }) {
  const errors = issues.filter((i) => i.level === "error").length;
  return (
    <section aria-label="Problems" className="max-h-56 shrink-0 overflow-y-auto border-t border-line-soft bg-panel">
      <header className="flex items-center gap-2 px-5 pt-4 pb-2">
        <IssueIcon level={errors ? "error" : "warning"} />
        <h2 className="text-sm font-semibold">{errors ? "Fix before running" : "Worth a look"}</h2>
        <span className="text-sm text-text-3">{countIssues(issues)}</span>
        <Button size="xs" variant="ghost" className="ml-auto" onClick={onHide}>
          Hide
        </Button>
      </header>
      <ul className="pb-2">
        {issues.map((i, n) => {
          const where = (i.steps ?? []).map(titleOf).join(", ");
          return (
            <li key={`${i.code}-${n}`}>
              <button
                className="flex w-full items-start gap-3 px-5 py-2.5 text-left hover:bg-raised disabled:cursor-default disabled:hover:bg-transparent"
                onClick={() => i.steps?.[0] && onPick(i.steps[0])}
                disabled={!i.steps?.length}
              >
                <IssueIcon level={i.level} className="mt-0.5" />
                <span className="min-w-0 flex-1 text-sm">
                  {where && <span className="font-medium">{where}: </span>}
                  <span className="text-text-2">{i.message}</span>
                </span>
                <span className="shrink-0 text-xs text-text-3">{i.level === "error" ? "Problem" : "Warning"}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function WorkflowRuns({ workflowId, onRun }: { workflowId: string; onRun?: () => void }) {
  const { data: runs, isLoading } = useRuns({ workflow_id: workflowId });
  if (isLoading) return null;
  if (!runs?.length) {
    return (
      <EmptyState
        icon={History}
        title="No runs yet"
        action={
          onRun && (
            <Button variant="primary" onClick={onRun}>
              <Play /> Run
            </Button>
          )
        }
      >
        Run this workflow on a task to see its runs here.
      </EmptyState>
    );
  }
  return (
    <div className="px-8 py-6">
      <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
        {runs.map((r) => (
          <li key={r.id}>
            <Link to={`/runs/${r.id}`} className="flex items-center gap-4 px-5 py-4 hover:bg-raised">
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-base">
                  <span className="truncate">
                    Run {r.number}: {r.task_title}
                  </span>
                  <span className="shrink-0 rounded-full bg-raised px-2 text-xs font-medium text-text-2">{r.workflow_version ? `Version ${r.workflow_version}` : "Draft"}</span>
                </span>
                <span className="mt-0.5 block truncate text-sm text-text-2">
                  <span className="mono mr-2 text-text-3">{r.task_key}</span>
                  {[relativeTime(r.started_at ?? r.created_at), formatDuration(r.duration_ms ?? elapsed(r.started_at)), r.integration_status === "merged" ? "Merged" : r.integration_status === "discarded" ? "Discarded" : null]
                    .filter(Boolean)
                    .join(", ")}
                </span>
              </span>
              <StatusPill state={runState(r.status)} label={RUN_LABEL[r.status]} />
              <ChevronRight className="size-4 shrink-0 text-text-3" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EditorSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col" aria-busy="true" aria-label="Loading the workflow">
      <header className="shrink-0 space-y-2 px-8 pt-7 pb-4">
        <div className="h-7 w-64 animate-pulse rounded-[8px] bg-raised" />
        <div className="h-4 w-96 max-w-full animate-pulse rounded-[6px] bg-raised" />
      </header>
      <div className="flex min-h-0 flex-1 border-t border-line-soft">
        <div className="canvas-grid relative flex-1">
          {[0, 1, 2].map((i) => (
            <div key={i} className="absolute h-[80px] w-[232px] animate-pulse rounded-[12px] bg-raised" style={{ left: "calc(50% - 116px)", top: 90 + i * 140 }} />
          ))}
        </div>
        <div className={cn("hidden w-[380px] shrink-0 space-y-4 border-l border-line-soft bg-panel p-6 min-[1181px]:block")}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded-control bg-raised" />
          ))}
        </div>
      </div>
    </div>
  );
}
