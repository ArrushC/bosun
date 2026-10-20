import { History } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useRuns } from "@/api/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState, ErrorNote, Spinner, StatusPill } from "@/components/ui/display";
import { SearchInput, Segmented } from "@/components/ui/inputs";
import { RUN_LABEL, runState } from "@/lib/status";
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
      <PageHeader title="Runs">
        <div className="flex flex-wrap items-center gap-3 px-8 pb-5">
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter runs" aria-label="Filter runs" className="w-72" />
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
        {error && <div className="px-8 py-4"><ErrorNote title="Runs could not be loaded">{(error as Error).message}</ErrorNote></div>}
        {!isLoading && rows.length === 0 && (
          <EmptyState icon={History} title={runs.length ? "No runs match" : "No runs yet"}>
            {runs.length ? "Try another filter." : "Open a task and choose Run with agent."}
          </EmptyState>
        )}
        {rows.length > 0 && (
          <div className="mx-auto max-w-[1180px] px-8 pt-2 pb-12">
            <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
              {rows.map((r) => {
                const tokens = Number(r.usage.output_tokens ?? 0);
                const details = [
                  r.mode === "single" ? "Single agent" : r.strategy === "team" ? "Agent team" : r.mode === "auto" ? "Automatic" : "Agent team",
                  r.backend === "demo" ? "Demo" : r.provider === "subscription" ? "Subscription" : r.provider === "api_key" ? "API key" : "Vertex AI",
                  relativeTime(r.started_at ?? r.created_at),
                  formatDuration(r.duration_ms ?? elapsed(r.started_at)),
                  tokens ? `${formatTokens(tokens)} tokens out` : null,
                  r.cost_usd != null ? `$${r.cost_usd.toFixed(2)}` : null,
                  r.integration_status === "merged" ? "Merged" : r.integration_status === "discarded" ? "Discarded" : null,
                ].filter(Boolean);
                return (
                  <li key={r.id}>
                    <Link to={`/runs/${r.id}`} className="flex items-center gap-5 px-5 py-4 transition-colors hover:bg-raised/50">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base">
                          Run {r.number}: {r.task_title}
                        </p>
                        <p className="mt-1 truncate text-xs text-text-3">
                          <span className="mono mr-2">{r.task_key}</span>
                          {details.join(", ")}
                        </p>
                      </div>
                      <StatusPill state={runState(r.status)} label={RUN_LABEL[r.status]} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
