import { Globe2, Waypoints } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useOverviewGraph, useRun, useRuns } from "@/api/hooks";
import { AgentGraph } from "@/components/graph/AgentGraph";
import { useOpenTask } from "@/components/layout/AppShell";
import { RunGraphView } from "@/components/run/RunGraphView";
import { Button } from "@/components/ui/button";
import { EmptyState, Spinner, StatusPill } from "@/components/ui/display";
import type { GraphEdge, GraphNode } from "@/lib/graph/reduce";
import { RUN_LABEL, roleColor, runState, sessionState, type VisualState } from "@/lib/status";
import { cn, relativeTime } from "@/lib/utils";
import { useEvents, useRunLog } from "@/stores/events";
import { useUi } from "@/stores/ui";

const ACTIVE = new Set(["queued", "preparing", "planning", "running", "waiting_approval"]);

function taskState(status: string): VisualState {
  return (
    { in_progress: "running", review: "waiting_approval", blocked: "failed", done: "success" } as Record<string, VisualState>
  )[status] ?? "pending";
}

function Overview() {
  const { data, isLoading } = useOverviewGraph();
  const direction = useUi((s) => s.graph.direction);
  const openTask = useOpenTask();
  const [, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(null);

  const { nodes, edges } = useMemo(() => {
    const nodes: GraphNode[] = [];
    for (const n of data?.nodes ?? []) {
      const d = n.data as Record<string, string>;
      if (n.type === "task") {
        nodes.push({ id: String(n.id), kind: "task", label: d.title, sublabel: d.key, state: taskState(d.status), firstSeq: 0, lastSeq: 0, data: {} });
      } else if (n.type === "run") {
        nodes.push({
          id: String(n.id),
          kind: "outcome",
          label: `Run ${d.number}`,
          sublabel: `${RUN_LABEL[d.status] ?? d.status}, ${d.strategy ?? d.mode}`,
          state: runState(d.status),
          firstSeq: 0,
          lastSeq: 0,
          data: {},
        });
      } else {
        nodes.push({
          id: String(n.id),
          kind: d.kind === "coordinator" ? "coordinator" : "agent",
          label: String(d.name).split(": ")[0],
          sublabel: String(d.name).split(": ").slice(1).join(": ") || d.role,
          state: sessionState(d.status),
          color: roleColor(d.role),
          firstSeq: 0,
          lastSeq: 0,
          data: { role: d.role, run_id: d.run_id },
        });
      }
    }
    const edges: GraphEdge[] = (data?.edges ?? []).map((e) => ({
      id: String(e.id),
      source: String(e.source),
      target: String(e.target),
      kind: (e.kind === "depends" ? "depends" : e.kind === "delegated" ? "delegated" : "assigned") as GraphEdge["kind"],
      seq: 0,
      active: false,
    }));
    for (const e of edges) {
      const target = nodes.find((n) => n.id === e.target);
      e.active = !!target && target.state === "running";
    }
    return { nodes, edges };
  }, [data]);

  if (isLoading) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  if (!nodes.length) {
    return (
      <EmptyState icon={Globe2} title="Nothing to show yet" className="h-full">
        The overview shows tasks with recent runs or dependencies, and the agents working on them.
      </EmptyState>
    );
  }
  return (
    <AgentGraph
      nodes={nodes}
      edges={edges}
      direction={direction}
      selectedId={selected}
      focusId={null}
      expanded={new Set()}
      onSelect={(id) => {
        setSelected(id);
        if (!id) return;
        const node = nodes.find((n) => n.id === id);
        if (node?.kind === "task") openTask(id);
        else if (node?.kind === "outcome") setParams({ run: id });
        else if (node?.data.run_id) setParams({ run: String(node.data.run_id) });
      }}
      onToggleExpand={() => {}}
      onToggleCollapse={() => {}}
      showMinimap
      follow={false}
      fitSignal={0}
    />
  );
}

function RunGraph({ runId }: { runId: string }) {
  const { data: run } = useRun(runId);
  const log = useRunLog(runId);
  const load = useEvents((s) => s.load);
  useEffect(() => {
    void load(runId);
  }, [runId, load]);
  if (!run) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line-soft px-4 py-2">
        <StatusPill state={runState(run.status)} label={RUN_LABEL[run.status]} />
        <p className="min-w-0 truncate text-sm">
          <span className="mono mr-2 text-xs text-text-3">{run.task_key}</span>
          Run {run.number}: {run.task_title}
        </p>
        <Button size="sm" variant="outline" className="ml-auto" asChild>
          <Link to={`/runs/${run.id}`}>Open run</Link>
        </Button>
      </div>
      <RunGraphView run={run} events={log.events} loading={log.loading} />
    </div>
  );
}

export function GraphPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run");
  const { data: runs = [] } = useRuns();
  const active = runs.filter((r) => ACTIVE.has(r.status));
  const recent = runs.filter((r) => !ACTIVE.has(r.status)).slice(0, 30);

  useEffect(() => {
    if (!runId && active.length === 1 && !params.has("overview")) setParams({ run: active[0].id }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active.length]);

  return (
    <div className="flex h-full min-h-0">
      <aside aria-label="Runs" className="flex w-64 shrink-0 flex-col border-r border-line-soft bg-panel">
        <div className="flex h-12 items-center gap-2 border-b border-line-soft px-4">
          <Waypoints className="size-4 text-text-2" />
          <h1 className="text-sm font-semibold">Agent graph</h1>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <button
            onClick={() => setParams({ overview: "1" })}
            className={cn("mb-2 flex w-full items-center gap-2 rounded-control px-2 py-2 text-left text-sm hover:bg-raised", !runId && "bg-raised")}
          >
            <Globe2 className="size-4 text-text-2" /> All tasks and agents
          </button>
          {active.length > 0 && <p className="px-2 pt-2 pb-1 text-2xs text-text-3">Live</p>}
          {active.map((r) => (
            <RunItem key={r.id} run={r} selected={runId === r.id} onClick={() => setParams({ run: r.id })} />
          ))}
          {recent.length > 0 && <p className="px-2 pt-3 pb-1 text-2xs text-text-3">Recent</p>}
          {recent.map((r) => (
            <RunItem key={r.id} run={r} selected={runId === r.id} onClick={() => setParams({ run: r.id })} />
          ))}
        </div>
      </aside>
      <div className="min-w-0 flex-1">{runId ? <RunGraph runId={runId} /> : <Overview />}</div>
    </div>
  );
}

function RunItem({ run, selected, onClick }: { run: ReturnType<typeof useRuns>["data"] extends (infer T)[] | undefined ? T : never; selected: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cn("flex w-full items-start gap-2 rounded-control px-2 py-1.5 text-left hover:bg-raised", selected && "bg-raised")}>
      <StatusPill state={runState(run.status)} compact className="mt-0.5" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{run.task_title}</span>
        <span className="block text-2xs text-text-3">
          <span className="mono">{run.task_key}</span> run {run.number}, {relativeTime(run.started_at ?? run.created_at)}
        </span>
      </span>
    </button>
  );
}
