import { ArrowUpRight, FolderGit2, Inbox, Plus, Radar, ScanSearch, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { useDashboard, useDemo, useWorkspaceMutations } from "@/api/hooks";
import type { RunListItem } from "@/api/types";
import { ThroughputChart } from "@/components/dashboard/ThroughputChart";
import { useOpenTask } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { ApprovalCard } from "@/components/run/ApprovalCard";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Spinner, StatusPill } from "@/components/ui/display";
import { RUN_LABEL, runState } from "@/lib/status";
import { cn, elapsed, formatDuration, formatTokens, relativeTime } from "@/lib/utils";
import { useUi } from "@/stores/ui";

function Panel({ title, action, children, className }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("min-w-0", className)}>
      <header className="mb-4 flex items-center gap-2">
        <h2 className="text-md font-semibold">{title}</h2>
        <div className="ml-auto">{action}</div>
      </header>
      {children}
    </section>
  );
}

function RunLine({ run, live }: { run: RunListItem; live?: boolean }) {
  return (
    <Link
      to={`/runs/${run.id}`}
      className="group grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-4 transition-colors hover:bg-raised/50"
    >
      <div className="min-w-0">
        <p className="truncate text-base">{run.task_title}</p>
        <p className="mt-1 flex items-center gap-2 text-xs text-text-3">
          <span className="mono">{run.task_key}</span>
          <span>
            {run.sessions} agent{run.sessions === 1 ? "" : "s"}
            {run.backend === "demo" ? ", demo" : ""}
          </span>
          <span>{live ? formatDuration(elapsed(run.started_at)) : relativeTime(run.ended_at ?? run.created_at)}</span>
        </p>
      </div>
      <StatusPill state={runState(run.status)} label={RUN_LABEL[run.status]} />
    </Link>
  );
}

export function DashboardPage() {
  const { data, isLoading, error } = useDashboard();
  const demo = useDemo();
  const { scan } = useWorkspaceMutations();
  const setCreate = useUi((s) => s.setCreate);
  const openTask = useOpenTask();

  if (isLoading) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  if (error || !data) return <div className="p-6"><ErrorNote title="The overview could not be loaded">{(error as Error | null)?.message}</ErrorNote></div>;

  const c = data.counts;
  const strip = [
    { label: "Agents running", value: c.active_agents, to: "/graph", tone: c.active_agents ? "text-st-run" : "" },
    { label: "Waiting for approval", value: c.pending_approvals, to: "/runs", tone: c.pending_approvals ? "text-st-approval" : "" },
    { label: "In review", value: c.review, to: "/board", tone: "" },
    { label: "Blocked", value: c.blocked, to: "/board", tone: c.blocked ? "text-st-fail" : "" },
    { label: "Discovered, not triaged", value: c.untriaged, to: "/inbox", tone: "" },
  ];
  const rateLimits = (data.usage.rate_limits ?? []) as Array<{ type?: string; status?: string; utilization?: number | null; resets_at?: number | null }>;
  const isEmpty = c.tasks === 0 && data.recent_runs.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Overview"
        subtitle={`${c.workspaces} workspace${c.workspaces === 1 ? "" : "s"}, ${c.tasks} open task${c.tasks === 1 ? "" : "s"}, ${c.connections} GitHub connection${c.connections === 1 ? "" : "s"}`}
        actions={
          <Button variant="primary" onClick={() => setCreate(true)}>
            <Plus /> New task
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1180px] space-y-12 px-8 pt-2 pb-12">
          <nav aria-label="Status" className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {strip.map((s) => (
              <Link
                key={s.label}
                to={s.to}
                className="flex flex-col gap-1 rounded-panel bg-panel px-5 py-4 transition-colors hover:bg-raised"
              >
                <span className={cn("text-xl font-semibold tabular-nums", s.tone)}>{s.value}</span>
                <span className="text-sm text-text-2">{s.label}</span>
              </Link>
            ))}
          </nav>

          {isEmpty && (
            <section className="rounded-panel border border-dashed border-line px-8 py-8">
              <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-center">
                <Radar className="size-8 text-accent" strokeWidth={1.5} />
                <div className="flex-1">
                  <p className="text-md font-semibold">Start with your own work, or explore a demo</p>
                  <p className="mt-1 text-sm leading-6 text-text-2">
                    Promote TODOs and GitHub issues from the Inbox, or load a demo project whose agents run without calling Claude.
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" asChild>
                    <Link to="/inbox">
                      <Inbox /> Open Inbox
                    </Link>
                  </Button>
                  <Button variant="primary" onClick={() => demo.seed.mutate()} disabled={demo.seed.isPending}>
                    <Sparkles /> Load demo
                  </Button>
                </div>
              </div>
            </section>
          )}

          <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-12">
              <Panel title="Live now" action={<Link to="/graph" className="text-sm text-text-2 hover:text-text">Agent graph</Link>}>
                {data.active_runs.length ? (
                  <div className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">{data.active_runs.map((r) => <RunLine key={r.id} run={r} live />)}</div>
                ) : (
                  <p className="rounded-panel bg-panel px-5 py-5 text-sm text-text-3">
                    No agents are running. Open a task and choose Run with agent.
                  </p>
                )}
              </Panel>
              <Panel title="Recent results" action={<Link to="/runs" className="text-sm text-text-2 hover:text-text">All runs</Link>}>
                {data.recent_runs.length ? (
                  <div className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">{data.recent_runs.map((r) => <RunLine key={r.id} run={r} />)}</div>
                ) : (
                  <p className="text-sm text-text-3">Finished runs will appear here.</p>
                )}
              </Panel>
              <Panel title="Activity">
                {data.activity.length ? (
                  <ol className="space-y-3">
                    {data.activity.slice(0, 6).map((a) => (
                      <li key={String(a.id)} className="flex items-baseline gap-4 text-sm">
                        <span className="w-24 shrink-0 text-xs text-text-3">{relativeTime(String(a.created_at))}</span>
                        <button onClick={() => openTask(String(a.task_id))} className="mono shrink-0 text-xs text-text-2 hover:text-text">
                          {String(a.task_key)}
                        </button>
                        <span className="min-w-0 truncate text-text-2">{String(a.message)}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-sm text-text-3">Nothing has happened yet.</p>
                )}
              </Panel>
            </div>

            <div className="space-y-12">
              <Panel title="Waiting for you">
                {data.pending_approvals.length ? (
                  <div className="space-y-3">
                    {data.pending_approvals.map((a) => (
                      <ApprovalCard key={a.id} approval={a} compact />
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-text-3">No approvals are pending.</p>
                )}
              </Panel>
              <Panel title="Runs finished, last 14 days">
                <ThroughputChart data={data.throughput as never} />
              </Panel>
              <Panel title="Usage this week">
                <div className="space-y-3 text-sm">
                  <p className="leading-6 text-text-2">
                    {formatTokens(Number(data.usage.input_tokens ?? 0) + Number(data.usage.cache_read_input_tokens ?? 0))} input and{" "}
                    {formatTokens(Number(data.usage.output_tokens ?? 0))} output tokens across {Number(data.usage.num_turns ?? 0)} model turns.
                  </p>
                  {rateLimits.length > 0 ? (
                    <ul className="space-y-2">
                      {rateLimits.map((r) => (
                        <li key={r.type} className="flex items-center gap-2 text-xs">
                          <span
                            className="size-2 rounded-full"
                            style={{ background: r.status === "rejected" ? "var(--st-fail)" : r.status === "allowed_warning" ? "var(--st-approval)" : "var(--st-ok)" }}
                          />
                          <span className="text-text">{(r.type ?? "limit").replace(/_/g, " ")} window</span>
                          <span className="text-text-3">
                            {r.status === "rejected" ? "limit reached" : r.status === "allowed_warning" ? "approaching limit" : "within limit"}
                            {r.utilization != null ? `, ${Math.round(r.utilization * 100)}% used` : ""}
                          </span>
                          {r.resets_at && <span className="ml-auto text-text-3">resets {relativeTime(new Date(r.resets_at * 1000).toISOString())}</span>}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-text-3">Subscription limit status appears after the first Claude run.</p>
                  )}
                </div>
              </Panel>
              <Panel title="Workspaces" action={<Link to="/integrations" className="text-sm text-text-2 hover:text-text">Manage</Link>}>
                <ul className="space-y-3">
                  {data.workspaces.map((w) => (
                    <li key={w.id} className="flex items-center gap-3 text-sm">
                      <FolderGit2 className={cn("size-4 shrink-0", w.status === "missing" ? "text-st-fail" : "text-text-3")} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate">{w.name}</p>
                        <p className="text-xs text-text-3">
                          {w.status === "missing" ? "Directory missing" : `${w.open_items} discovered, scanned ${relativeTime(w.last_scanned_at)}`}
                        </p>
                      </div>
                      <Button size="icon-sm" variant="ghost" aria-label={`Scan ${w.name}`} onClick={() => scan.mutate(w.id)} disabled={scan.isPending}>
                        <ScanSearch />
                      </Button>
                    </li>
                  ))}
                  {data.workspaces.length === 0 && (
                    <EmptyState title="No workspaces" className="py-4">
                      <Link to="/integrations" className="inline-flex items-center gap-1 text-accent-strong">
                        Add a workspace <ArrowUpRight className="size-3" />
                      </Link>
                    </EmptyState>
                  )}
                </ul>
              </Panel>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
