import { ArrowLeft, Ban, Pause, Play, RotateCcw, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
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
import { RUN_LABEL, runState } from "@/lib/status";
import { elapsed, formatDuration, formatTokens } from "@/lib/utils";
import { useEvents, useRunLog } from "@/stores/events";

function RunControls({ run }: { run: Run }) {
  const action = useRunAction(run.id);
  const busy = action.isPending;
  if (run.active) {
    return (
      <>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => action.mutate({ action: "pause" })}>
          <Pause /> Pause
        </Button>
        <Button size="sm" variant="danger" disabled={busy} onClick={() => action.mutate({ action: "cancel" })}>
          <Ban /> Cancel
        </Button>
      </>
    );
  }
  return (
    <>
      {(run.status === "paused" || run.status === "interrupted") && (
        <Button size="sm" variant="primary" disabled={busy} onClick={() => action.mutate({ action: "resume" })}>
          <Play /> Resume
        </Button>
      )}
      {(run.status === "failed" || run.status === "interrupted") && (
        <Button size="sm" variant={run.status === "failed" ? "primary" : "secondary"} disabled={busy} onClick={() => action.mutate({ action: "retry" })}>
          <RotateCcw /> Retry
        </Button>
      )}
      {["paused", "interrupted", "failed", "queued"].includes(run.status) && (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => action.mutate({ action: "cancel" })}>
          <Ban /> Cancel run
        </Button>
      )}
    </>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-text-3">{label}</span>
      <span className="text-text">{children}</span>
    </span>
  );
}

export function RunPage() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "graph";
  const sessionParam = params.get("session");
  const { data: run, isLoading, error } = useRun(runId);
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
  const advice = run.status === "failed" ? adviceFor(run.error_kind) : null;
  const tokensIn = Number(run.usage.input_tokens ?? 0) + Number(run.usage.cache_read_input_tokens ?? 0) + Number(run.usage.cache_creation_input_tokens ?? 0);
  const tokensOut = Number(run.usage.output_tokens ?? 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-line-soft">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pt-3 pb-2">
          <Link to="/runs" className="rounded-control p-1 text-text-3 hover:bg-raised hover:text-text" aria-label="All runs">
            <ArrowLeft className="size-4" />
          </Link>
          <button onClick={() => openTask(run.task_id)} className="mono text-xs text-text-3 hover:text-text-2">
            {run.task_key}
          </button>
          <h1 className="min-w-0 truncate text-md font-semibold tracking-tight">
            Run {run.number}: {run.task_title}
          </h1>
          <StatusPill state={runState(run.status)} label={RUN_LABEL[run.status]} />
          {run.is_demo && <span className="rounded-[4px] bg-raised px-1.5 text-2xs text-text-2">demo</span>}
          <div className="ml-auto flex items-center gap-2">
            <RunControls run={run} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-5 pb-2.5 text-xs">
          <Stat label="Strategy">{run.strategy === "team" ? "Agent team" : run.mode === "auto" ? (run.strategy ? "Auto: single agent" : "Auto: planning") : run.mode === "team" ? "Agent team" : "Single agent"}</Stat>
          <Stat label="Backend">{run.backend === "demo" ? "Demo" : run.provider === "subscription" ? "Claude subscription" : run.provider === "api_key" ? "Anthropic API" : "Vertex AI"}</Stat>
          {run.model && <Stat label="Model"><span className="mono">{run.model}</span></Stat>}
          <Stat label="Branch">{run.branch ? <span className="mono">{run.branch}</span> : run.isolation === "in_place" ? "edits in place" : "preparing"}</Stat>
          <Stat label="Elapsed">{formatDuration(elapsed(run.started_at, run.ended_at))}</Stat>
          {tokensOut > 0 && <Stat label="Tokens">{formatTokens(tokensIn)} in, {formatTokens(tokensOut)} out</Stat>}
          {run.cost_usd != null && <Stat label="Cost">${run.cost_usd.toFixed(2)}</Stat>}
        </div>
        {(pending.length > 0 || advice || run.status_reason) && (
          <div className="space-y-2 px-5 pb-3">
            {advice && (
              <ErrorNote title={advice.title}>
                <span className="block">{run.error}</span>
                <span className="mt-1 block text-text">{advice.fix}</span>
              </ErrorNote>
            )}
            {!advice && run.status_reason && !run.active && run.status !== "completed" && (
              <p className="text-xs text-text-2">{run.status_reason}</p>
            )}
            {pending.length > 0 && (
              <div className="grid gap-2 lg:grid-cols-2">
                {pending.map((a) => (
                  <ApprovalCard key={a.id} approval={a} compact={a.kind === "tool_permission"} />
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
          { value: "graph", label: "Agent graph" },
          { value: "transcript", label: "Transcripts", count: run.sessions.length },
          { value: "changes", label: "Changes", count: Number((run.usage.diff as { files?: number } | undefined)?.files ?? 0) },
          { value: "summary", label: "Summary" },
        ]}
      >
        <TabPanel value="graph" className="flex flex-col overflow-hidden">
          {log.error ? (
            <div className="p-5">
              <ErrorNote title="Event history could not be loaded" action={<Button size="sm" onClick={() => void load(runId, true)}>Retry</Button>}>
                {log.error}
              </ErrorNote>
            </div>
          ) : (
            <RunGraphView run={run} events={log.events} loading={log.loading} />
          )}
        </TabPanel>
        <TabPanel value="transcript" className="overflow-hidden">
          <Transcript
            run={run}
            events={log.events}
            sessionId={sessionParam}
            onSelectSession={(id) => {
              const next = new URLSearchParams(params);
              next.set("session", id);
              setParams(next, { replace: true });
            }}
          />
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
