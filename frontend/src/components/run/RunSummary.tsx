import { ListTree } from "lucide-react";

import type { Run } from "@/api/types";
import { KeyValue, Md, Notice, RoleTag, StatusPill } from "@/components/ui/display";
import { roleColor, subtaskState } from "@/lib/status";
import { formatDuration, formatTokens, relativeTime } from "@/lib/utils";

export function RunSummary({ run }: { run: Run }) {
  const config = run.config as {
    mode?: string;
    single_role?: string;
    team_roles?: string[];
    max_turns?: number;
    timeout_minutes?: number;
    require_plan_approval?: boolean;
    max_team_size?: number;
    allow_subagents?: boolean;
    instructions?: string | null;
    plan?: { summary?: string; rationale?: string };
    plan_warnings?: string[];
    remembered_approvals?: Array<{ tool: string; command?: string }>;
    scope?: Record<string, { name: string; description: string }>;
  };
  const usage = run.usage as Record<string, number | undefined>;

  return (
    <div className="mx-auto grid max-w-5xl gap-6 px-6 py-5 lg:grid-cols-[1fr_300px]">
      <div className="min-w-0 space-y-5">
        <section>
          <h2 className="mb-2 text-sm font-semibold">Result</h2>
          {run.summary ? (
            <div className="rounded-[10px] border border-line-soft bg-panel px-4 py-3">
              <Md>{run.summary}</Md>
            </div>
          ) : (
            <p className="text-sm text-text-3">{run.active ? "The summary appears when the run finishes." : "No summary was produced."}</p>
          )}
          {run.status === "completed" && (
            <Notice className="mt-3">
              The agents finished. Review the changes, then merge them or send a follow-up. Mark the task done from the board when you are satisfied.
            </Notice>
          )}
        </section>
        {run.subtasks.length > 0 && (
          <section>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <ListTree className="size-4 text-text-3" /> Plan
            </h2>
            {config.plan?.summary && <Md className="mb-3 text-[13px] text-text-2">{config.plan.summary}</Md>}
            {config.plan_warnings?.length ? <Notice tone="warn" className="mb-3">{config.plan_warnings.join(" ")}</Notice> : null}
            <ol className="divide-y divide-line-soft rounded-[10px] border border-line-soft">
              {run.subtasks.map((s) => (
                <li key={s.id} className="flex items-start gap-3 px-3 py-2.5">
                  <span className="mono mt-0.5 text-xs text-text-3">{s.key}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{s.title}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-text-3">
                      <RoleTag name={s.role} color={roleColor(s.role)} />
                      {s.depends_on.length > 0 && <span>after {s.depends_on.join(", ")}</span>}
                      {s.attempts > 1 && <span>{s.attempts} attempts</span>}
                    </p>
                    {s.error && <p className="mt-1 text-xs text-st-fail">{s.error}</p>}
                  </div>
                  <StatusPill state={subtaskState(s.status)} />
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
      <aside className="space-y-5">
        <section>
          <h2 className="mb-2 text-sm font-semibold">Usage</h2>
          <KeyValue
            items={[
              ["Agent sessions", String(usage.sessions ?? run.sessions.length)],
              ["Model turns", String(usage.num_turns ?? 0)],
              ["Input tokens", formatTokens((usage.input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0))],
              ["Output tokens", formatTokens(usage.output_tokens ?? 0)],
              ["Duration", formatDuration(run.started_at && run.ended_at ? new Date(run.ended_at).getTime() - new Date(run.started_at).getTime() : null)],
              [
                "Billing",
                run.backend === "demo"
                  ? "None (demo)"
                  : run.provider === "subscription"
                    ? "Claude subscription limits"
                    : run.cost_usd != null
                      ? `$${run.cost_usd.toFixed(2)} estimated`
                      : "Billed per token",
              ],
            ]}
          />
        </section>
        <section>
          <h2 className="mb-2 text-sm font-semibold">Configuration</h2>
          <KeyValue
            items={[
              ["Mode", String(config.mode ?? run.mode)],
              ...(config.mode === "single" ? [["Role", String(config.single_role)] as [string, string]] : [["Roles", (config.team_roles ?? []).join(", ")] as [string, string]]),
              ["Max turns", String(config.max_turns ?? "")],
              ["Time limit", `${config.timeout_minutes ?? "?"} min per session`],
              ...(config.mode !== "single" ? [["Plan approval", config.require_plan_approval ? "Required" : "Off"] as [string, string]] : []),
              ["Isolation", run.isolation === "worktree" ? "Git worktree" : "In place"],
              ["Created", relativeTime(run.created_at)],
            ]}
          />
          {config.instructions && <p className="mt-2 text-xs text-text-2">Extra instructions: {config.instructions}</p>}
          {config.remembered_approvals?.length ? (
            <p className="mt-2 text-xs text-text-2">
              Allowed for this run: {config.remembered_approvals.map((r) => r.command ?? r.tool).join(", ")}
            </p>
          ) : null}
        </section>
      </aside>
    </div>
  );
}
