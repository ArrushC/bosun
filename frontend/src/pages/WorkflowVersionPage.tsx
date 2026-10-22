import { ArrowLeft, Lock, PencilLine, Play, RefreshCw, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { useRestoreVersion, useRoles, useWorkflow, useWorkflowVersion } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Notice, Spinner } from "@/components/ui/display";
import { Dialog, Tip } from "@/components/ui/overlays";
import { RunWorkflowDialog } from "@/components/workflow/RunWorkflowDialog";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { AgentChangeNotes, AgentTags, ChangeList, draftPhrase, draftState, StepFacts, versionContent } from "@/components/workflow/versions";
import { cn, plural, relativeTime } from "@/lib/utils";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { hasChanges, normalizeStep, specChanges } from "@/lib/workflow";

/** A published version, read-only: what it runs, how it differs, and running or restoring it. */
export function WorkflowVersionPage() {
  const { workflowId = "", number = "" } = useParams();
  const n = Number(number);
  const valid = Number.isInteger(n) && n > 0;
  const navigate = useNavigate();
  const workflow = useWorkflow(workflowId);
  const version = useWorkflowVersion(workflowId, valid ? n : null);
  // What changed since the version before, once this one is known to exist.
  const previous = useWorkflowVersion(workflowId, version.data && n > 1 ? n - 1 : null);
  const { data: roles = [] } = useRoles();
  const restore = useRestoreVersion(workflowId);
  const narrow = useMediaQuery("(max-width: 1180px)");
  const [selected, setSelected] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);

  const v = version.data;
  const content = useMemo(() => (v ? versionContent(v) : null), [v]);
  const draft = useMemo(() => (workflow.data ? { name: workflow.data.name, description: workflow.data.description, steps: (workflow.data.spec.steps ?? []).map(normalizeStep) } : null), [workflow.data]);
  const sinceLast = useMemo(() => (content && previous.data ? specChanges(versionContent(previous.data), content) : null), [content, previous.data]);
  const draftDiffers = content && draft ? hasChanges(specChanges(content, draft)) : false;

  const failed = (workflow.error ?? version.error) as Error | null;
  const missing = (failed instanceof ApiError && failed.status === 404) || !valid;
  if (missing) {
    return (
      <EmptyState icon={TriangleAlert} title="This version doesn't exist" action={<Button asChild variant="secondary"><Link to={`/workflows/${workflowId}?tab=versions`}>All versions</Link></Button>}>
        The workflow may have been deleted, or it has no version {number}.
      </EmptyState>
    );
  }
  if (failed) {
    return (
      <div className="mx-auto max-w-xl px-8 py-16">
        <ErrorNote
          title="This version could not be loaded"
          action={
            <Button size="sm" variant="secondary" onClick={() => void Promise.all([workflow.refetch(), version.refetch()])}>
              <RefreshCw /> Try again
            </Button>
          }
        >
          {failed.message}
        </ErrorNote>
      </div>
    );
  }
  if (!v || !content || !workflow.data || !draft) {
    return (
      <div className="flex h-full items-center justify-center" aria-busy="true" aria-label="Loading the version">
        <Spinner />
      </div>
    );
  }

  const step = content.steps.find((s) => s.key === selected) ?? null;
  const agents = v.agents;
  const blocked = v.issues.length ? v.issues[0].message : null;
  const draftErrors = workflow.data.issues.filter((i) => i.level === "error");
  const showPanel = !narrow || panelOpen;
  const state = draftState(workflow.data, null);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-8 pt-6 pb-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <Link to={`/workflows/${workflowId}?tab=versions`} className="-ml-2 rounded-control p-2 text-text-3 hover:bg-raised hover:text-text" aria-label="All versions">
            <ArrowLeft className="size-5" />
          </Link>
          {/* Wide enough for the name and its badges; the actions wrap below it in a narrow window. */}
          <div className="min-w-0 flex-1 basis-[26rem]">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="truncate text-lg font-semibold tracking-tight">{v.name}</h1>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-raised px-2.5 py-0.5 text-xs font-medium text-text-2">
                <Lock className="size-3" /> Version {v.number}, read-only
              </span>
              {v.is_latest && <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-strong">Latest</span>}
            </div>
            <p className="mt-0.5 truncate text-sm text-text-2">
              Published {relativeTime(v.created_at)}
              {v.note ? `: ${v.note}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" asChild>
              <Link to={`/workflows/${workflowId}`}>
                <PencilLine /> Edit the draft
              </Link>
            </Button>
            <Button variant="secondary" onClick={() => setRestoreOpen(true)}>
              <RotateCcw /> Restore to draft
            </Button>
            <Tip label={blocked ?? `Run version ${v.number} on a task`}>
              <span tabIndex={blocked ? 0 : -1}>
                <Button variant="primary" disabled={!!blocked} onClick={() => setRunOpen(true)}>
                  <Play /> Run version {v.number}
                </Button>
              </span>
            </Tip>
          </div>
        </div>
        {(v.issues.length > 0 || v.agent_changes.some((c) => c.state === "changed")) && (
          <div className="mt-4 space-y-3">
            {v.issues.length > 0 && (
              <ErrorNote title={`Version ${v.number} can't run now`}>
                {v.issues.map((i) => i.message).join(" ")}
              </ErrorNote>
            )}
            <AgentChangeNotes changes={v.agent_changes} version={v.number} />
          </div>
        )}
      </header>

      <div className="relative flex min-h-0 flex-1 overflow-hidden border-t border-line-soft">
        <section aria-label={`Version ${v.number} steps`} className="relative min-w-0 flex-1">
          <WorkflowCanvas
            steps={content.steps}
            agents={[]}
            allRoles={agents}
            issues={[]}
            selected={selected}
            onSelect={(key) => {
              setSelected(key);
              if (key) setPanelOpen(true);
            }}
            onChange={() => {}}
            onAdd={() => {}}
            addMenuOpen={false}
            onAddMenuOpenChange={() => {}}
            readOnly
            panelHidden={!showPanel}
            coveredRight={narrow && showPanel ? 384 : 0}
            onShowPanel={() => setPanelOpen(true)}
          />
        </section>
        {showPanel && (
          <aside
            aria-label={step ? `${step.title || "Step"} details` : `Version ${v.number} details`}
            className={cn(
              "flex w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line-soft bg-panel",
              narrow && "absolute top-3 right-3 bottom-3 z-20 w-[min(360px,calc(100%-24px))] rounded-panel border shadow-pop",
            )}
          >
            {narrow && (
              <Button variant="ghost" size="icon-sm" className="absolute top-4 right-4" onClick={() => setPanelOpen(false)} aria-label="Hide the details panel">
                <X className="size-4" />
              </Button>
            )}
            <div className={cn("px-6 pt-5 pb-4", narrow && "pr-14")}>
              <h2 className="truncate text-md font-semibold">{step ? step.title.trim() || "Untitled step" : `Version ${v.number}`}</h2>
              <p className="mt-1 text-sm text-text-2">{step ? "As this version defines it. Edit the draft to change it." : `${plural(content.steps.length, "step")}. Select one to see it.`}</p>
            </div>
            <div className="space-y-6 px-6 pb-8">
              {step ? (
                <StepFacts step={step} steps={content.steps} agent={agents.find((a) => a.key === step.agent)} />
              ) : (
                <>
                  <section>
                    <h3 className="mb-2 text-sm font-medium">{n > 1 ? `Changes since version ${n - 1}` : "The first version"}</h3>
                    {n > 1 ? sinceLast ? <ChangeList changes={sinceLast} /> : <p className="text-sm text-text-3">Loading…</p> : <p className="text-sm text-text-2">{v.description || "The first published version of this workflow."}</p>}
                  </section>
                  <section>
                    <h3 className="mb-2 text-sm font-medium">Agents, as published</h3>
                    <AgentTags agents={agents} />
                  </section>
                  <section>
                    <h3 className="mb-2 text-sm font-medium">Runs</h3>
                    <p className="text-sm text-text-2">
                      {v.run_count ? (
                        <Link to={`/workflows/${workflowId}?tab=runs`} className="underline decoration-text-3/60 underline-offset-[3px] hover:text-text">
                          {plural(v.run_count, "run")} of this version
                        </Link>
                      ) : (
                        "Not run yet."
                      )}
                    </p>
                  </section>
                  <Notice>
                    The draft is <span className="font-medium">{draftPhrase(workflow.data, null)}</span>. Versions never change: editing the draft or publishing again leaves this one, and its runs, as they are.
                  </Notice>
                </>
              )}
            </div>
          </aside>
        )}
      </div>

      <Dialog
        open={restoreOpen}
        onOpenChange={setRestoreOpen}
        size="sm"
        title={`Replace the draft with version ${v.number}?`}
        description="The draft's name, description and steps become a copy of this version. Published versions don't change."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRestoreOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={restore.isPending || !draftDiffers}
              onClick={() =>
                restore.mutate(
                  { number: v.number, revision: workflow.data!.revision },
                  {
                    onSuccess: () => {
                      setRestoreOpen(false);
                      toast.success(`The draft is now a copy of version ${v.number}`);
                      navigate(`/workflows/${workflowId}`);
                    },
                  },
                )
              }
            >
              {restore.isPending ? <Spinner className="text-accent-fg" /> : <RotateCcw />} Replace the draft
            </Button>
          </>
        }
      >
        {draftDiffers && workflow.data.unpublished_changes ? (
          <Notice tone="warn">The draft has changes that aren't in any published version. Replacing it loses them.</Notice>
        ) : (
          <p className="text-sm text-text-2">{draftDiffers ? "The draft's current changes are kept in a published version, so nothing is lost." : "The draft already matches this version."}</p>
        )}
      </Dialog>

      <RunWorkflowDialog
        open={runOpen}
        onOpenChange={setRunOpen}
        workflowId={workflowId}
        name={workflow.data.name}
        revision={workflow.data.revision}
        steps={draft.steps}
        roles={roles}
        draftState={workflow.data.latest_version ? state.label.toLowerCase() : "not published"}
        draftProblem={draftErrors.length ? `Fix ${plural(draftErrors.length, "problem")} first` : null}
        initialSource={v.number}
      />
    </div>
  );
}
