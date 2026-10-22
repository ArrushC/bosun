import { ArrowLeft, Ban, Pause, Play, RotateCcw, TriangleAlert } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { useRun, useRunAction } from "@/api/hooks";
import type { Run } from "@/api/types";
import { useOpenTask } from "@/components/layout/AppShell";
import { ApprovalCard } from "@/components/run/ApprovalCard";
import { DiffView } from "@/components/run/DiffView";
import { RunGraphView } from "@/components/run/RunGraphView";
import { RunSummary } from "@/components/run/RunSummary";
import { Transcript } from "@/components/run/Transcript";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Spinner, StatusPill, TabPanel, Tabs } from "@/components/ui/display";
import { adviceFor } from "@/lib/errors";
import { RUN_LABEL, runState, strategyLabel } from "@/lib/status";
import { runWorkflow } from "@/lib/workflow";
import { elapsed, formatDuration } from "@/lib/utils";
import { useEvents, useRunLog } from "@/stores/events";

// The Steps view draws with the workflow canvas, which only workflow runs need.
const RunSteps = lazy(() => import("@/components/run/RunSteps").then((m) => ({ default: m.RunSteps })));

function RunControls({ run }: { run: Run }) {
  const action = useRunAction(run.id);
  const busy = action.isPending;
  if (run.active) {
    return (
      <>
        <Button variant="secondary" disabled={busy} onClick={() => action.mutate({ action: "pause" })}>
          <Pause /> Pause
        </Button>
        <Button variant="danger" disabled={busy} onClick={() => action.mutate({ action: "cancel" })}>
          <Ban /> Cancel
        </Button>
      </>
    );
  }
  return (
    <>
      {(run.status === "paused" || run.status === "interrupted") && (
        <Button variant="primary" disabled={busy} onClick={() => action.mutate({ action: "resume" })}>
          <Play /> Resume
        </Button>
      )}
      {(run.status === "failed" || run.status === "interrupted") && (
        <Button variant={run.status === "failed" ? "primary" : "secondary"} disabled={busy} onClick={() => action.mutate({ action: "retry" })}>
          <RotateCcw /> Retry
        </Button>
      )}
      {["paused", "interrupted", "failed", "queued"].includes(run.status) && (
        <Button variant="ghost" disabled={busy} onClick={() => action.mutate({ action: "cancel" })}>
          <Ban /> Cancel run
        </Button>
      )}
    </>
  );
}

export function RunPage() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const sessionParam = params.get("session");
  const stepParam = params.get("step");
  const { data: run, isLoading, error } = useRun(runId);
  const tab = params.get("tab") ?? (run?.mode === "workflow" ? "steps" : "graph");
  const log = useRunLog(runId);
  const load = useEvents((s) => s.load);
  const openTask = useOpenTask();
  const [, tick] = useState(0);

  useEffect(() => {
    if (runId) void load(runId);
  }, [runId, load]);
  useEffect(() => {
    if (!run?.active) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [run?.active]);

  const setTab = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next, { replace: true });
  };

  if (isLoading) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  if (error || !run) {
    return (
      <EmptyState icon={TriangleAlert} title="Run not found" action={<Button asChild variant="secondary"><Link to="/runs">All runs</Link></Button>}>
        {(error as Error | null)?.message}
      </EmptyState>
    );
  }

  const pending = run.approvals.filter((a) => a.status === "pending");
  const workflow = runWorkflow(run);
  const openTranscript = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", "transcript");
    next.set("session", id);
    setParams(next, { replace: true });
  };
  const selectStep = (key: string | null) => {
    const next = new URLSearchParams(params);
    next.set("tab", "steps");
    if (key) next.set("step", key);
    else next.delete("step");
    setParams(next, { replace: true });
  };
  const advice = run.status === "failed" ? adviceFor(run.error_kind, { workflow: !!workflow }) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-8 pt-7">
          <Link to="/runs" className="-ml-2 rounded-control p-2 text-text-3 hover:bg-raised hover:text-text" aria-label="All runs">
            <ArrowLeft className="size-5" />
          </Link>
          <h1 className="min-w-0 truncate text-lg font-semibold tracking-tight">
            Run {run.number}: {run.task_title}
          </h1>
          <StatusPill state={runState(run.status)} label={RUN_LABEL[run.status]} />
          {run.is_demo && <span className="rounded-full bg-raised px-2 text-xs text-text-2">demo</span>}
          <div className="ml-auto flex items-center gap-2">
            <RunControls run={run} />
          </div>
        </div>
        <p className="truncate px-8 pt-2 pb-6 text-sm text-text-3">
          <button onClick={() => openTask(run.task_id)} className="mono mr-3 text-text-2 hover:text-text">
            {run.task_key}
          </button>
          {workflow && (
            <>
              <Link to={`/workflows/${workflow.id}`} className="text-text-2 underline decoration-text-3/60 underline-offset-[3px] hover:text-text hover:decoration-text-2">
                Workflow {workflow.name}
              </Link>
              {", "}
              {workflow.version ? (
                <Link
                  to={`/workflows/${workflow.id}/versions/${workflow.version}`}
                  className="text-text-2 underline decoration-text-3/60 underline-offset-[3px] hover:text-text hover:decoration-text-2"
                >
                  version {workflow.version}
                </Link>
              ) : (
                <span className="text-text-2">draft (revision {workflow.revision})</span>
              )}
              {", "}
            </>
          )}
          {[
            ...(workflow ? [] : [strategyLabel(run)]),
            run.backend === "demo" ? "Demo" : run.provider === "subscription" ? "Claude subscription" : run.provider === "api_key" ? "Anthropic API" : "Vertex AI",
            formatDuration(elapsed(run.started_at, run.ended_at)),
          ].join(", ")}
          <span className="mono ml-3">{run.branch ?? (run.isolation === "in_place" ? "edits in place" : "preparing worktree")}</span>
        </p>
        {(pending.length > 0 || advice || run.status_reason) && (
          <div className="space-y-3 px-8 pb-6">
            {advice && (
              <ErrorNote title={advice.title}>
                <span className="block">{run.error}</span>
                <span className="mt-1 block text-text">{advice.fix}</span>
              </ErrorNote>
            )}
            {!advice && run.status_reason && !run.active && run.status !== "completed" && (
              <p className="text-sm text-text-2">{run.status_reason}</p>
            )}
            {pending.length > 0 && (
              <div className="grid gap-3 lg:grid-cols-2">
                {pending.map((a) => (
                  // Approval steps are shown in full in the Steps tab; here they only need the buttons.
                  <ApprovalCard key={a.id} approval={a} compact={a.kind === "tool_permission" || (a.kind === "workflow_step" && tab === "steps")} />
                ))}
              </div>
            )}
          </div>
        )}
      </header>

      <Tabs
        value={tab}
        onValueChange={setTab}
        className="min-h-0 flex-1"
        tabs={[
          ...(workflow ? [{ value: "steps", label: "Steps", count: run.subtasks.length || undefined }] : []),
          { value: "graph", label: "Agent graph" },
          { value: "transcript", label: "Transcripts", count: run.sessions.length },
          { value: "changes", label: "Changes", count: Number((run.usage.diff as { files?: number } | undefined)?.files ?? 0) },
          { value: "summary", label: "Summary" },
        ]}
      >
        {workflow && (
          <TabPanel value="steps" className="flex flex-col overflow-hidden">
            <Suspense fallback={<div className="flex flex-1 items-center justify-center"><Spinner /></div>}>
              <RunSteps run={run} selected={stepParam} onSelect={selectStep} onOpenTranscript={openTranscript} />
            </Suspense>
          </TabPanel>
        )}
        <TabPanel value="graph" className="flex flex-col overflow-hidden">
          {log.error ? (
            <div className="px-8 py-5">
              <ErrorNote title="Event history could not be loaded" action={<Button size="sm" onClick={() => void load(runId, true)}>Retry</Button>}>
                {log.error}
              </ErrorNote>
            </div>
          ) : (
            <RunGraphView run={run} events={log.events} loading={log.loading} />
          )}
        </TabPanel>
        <TabPanel value="transcript" className="overflow-hidden">
          <Transcript run={run} events={log.events} sessionId={sessionParam} onSelectSession={openTranscript} />
        </TabPanel>
        <TabPanel value="changes" className="overflow-hidden">
          <DiffView run={run} />
        </TabPanel>
        <TabPanel value="summary">
          <RunSummary run={run} />
        </TabPanel>
      </Tabs>
    </div>
  );
}
