import { History } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useRuns } from "@/api/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState, ErrorNote, RoleTag, Spinner, StatusPill } from "@/components/ui/display";
import { SearchInput, Segmented } from "@/components/ui/inputs";
import { RUN_LABEL, roleColor, runState } from "@/lib/status";
import { elapsed, formatDuration, formatTokens, relativeTime } from "@/lib/utils";

const FILTERS = {
  all: null,
  active: ["queued", "preparing", "planning", "running", "waiting_approval"],
  attention: ["failed", "interrupted", "paused", "waiting_approval"],
  finished: ["completed", "cancelled"],
} as const;

export function RunsPage() {
  const { data: runs = [], isLoading, error } = useRuns();
  const [filter, setFilter] = useState<keyof typeof FILTERS>("all");
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const allowed = FILTERS[filter] as readonly string[] | null;
    const needle = q.trim().toLowerCase();
    return runs.filter(
      (r) => (!allowed || allowed.includes(r.status)) && (!needle || `${r.task_key} ${r.task_title} ${r.roles.join(" ")}`.toLowerCase().includes(needle)),
    );
  }, [runs, filter, q]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Runs" subtitle="Every agent execution, newest first. Open a run to inspect, resume or continue it.">
        <div className="flex flex-wrap items-center gap-2 px-5 pb-2.5">
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter runs" aria-label="Filter runs" className="w-60" />
          <Segmented
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "All" },
              { value: "active", label: "Active" },
              { value: "attention", label: "Needs attention" },
              { value: "finished", label: "Finished" },
            ]}
          />
        </div>
      </PageHeader>
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading && <div className="flex justify-center p-10"><Spinner /></div>}
        {error && <div className="p-5"><ErrorNote title="Runs could not be loaded">{(error as Error).message}</ErrorNote></div>}
        {!isLoading && rows.length === 0 && (
          <EmptyState icon={History} title={runs.length ? "No runs match" : "No runs yet"}>
            {runs.length ? "Try another filter." : "Open a task and choose Run with agent."}
          </EmptyState>
        )}
        {rows.length > 0 && (
          <table className="w-full min-w-[960px] text-sm">
            <thead className="sticky top-0 z-10 bg-bg text-left text-xs text-text-3">
              <tr className="border-b border-line-soft">
                <th className="py-2 pl-5 font-medium">Status</th>
                <th className="py-2 font-medium">Task</th>
                <th className="py-2 font-medium">Agents</th>
                <th className="py-2 font-medium">Backend</th>
                <th className="py-2 font-medium">Started</th>
                <th className="py-2 font-medium">Duration</th>
                <th className="py-2 pr-5 text-right font-medium">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-line-soft hover:bg-panel">
                  <td className="py-2.5 pl-5">
                    <StatusPill state={runState(r.status)} label={RUN_LABEL[r.status]} />
                  </td>
                  <td className="max-w-[360px] py-2.5">
                    <Link to={`/runs/${r.id}`} className="block truncate hover:text-accent-strong">
                      <span className="mono mr-2 text-xs text-text-3">{r.task_key}</span>
                      Run {r.number}: {r.task_title}
                    </Link>
                    <span className="text-2xs text-text-3">
                      {r.mode === "single" ? "Single agent" : r.strategy === "team" ? "Agent team" : r.mode === "auto" ? "Automatic" : "Agent team"}
                      {r.integration_status === "merged" ? ", merged" : r.integration_status === "discarded" ? ", discarded" : ""}
                    </span>
                  </td>
                  <td className="py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {r.roles.map((role) => (
                        <RoleTag key={role} name={role} color={roleColor(role)} />
                      ))}
                      {r.sessions > r.roles.length && <span className="text-2xs text-text-3">{r.sessions} sessions</span>}
                    </div>
                  </td>
                  <td className="py-2.5 text-xs text-text-2">
                    {r.backend === "demo" ? "Demo" : r.provider === "subscription" ? "Subscription" : r.provider === "api_key" ? "API key" : "Vertex AI"}
                    {r.model && <span className="mono block text-2xs text-text-3">{r.model}</span>}
                  </td>
                  <td className="py-2.5 text-xs text-text-2">{relativeTime(r.started_at ?? r.created_at)}</td>
                  <td className="py-2.5 text-xs text-text-2">{formatDuration(r.duration_ms ?? elapsed(r.started_at))}</td>
                  <td className="py-2.5 pr-5 text-right text-xs text-text-2">
                    {formatTokens(Number(r.usage.output_tokens ?? 0))} out
                    {r.cost_usd != null && <span className="block text-2xs text-text-3">${r.cost_usd.toFixed(2)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
