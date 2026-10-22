import { ListTree } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import type { Run } from "@/api/types";
import { KeyValue, Md, Notice, RoleTag, StatusPill } from "@/components/ui/display";
import { roleColor, stepStatusLabel, strategyLabel, subtaskState } from "@/lib/status";
import { cn, formatDuration, formatTokens, plural, relativeTime } from "@/lib/utils";
import { runWorkflow } from "@/lib/workflow";

export function RunSummary({ run }: { run: Run }) {
  const config = run.config as {
    mode?: string;
    single_role?: string;
    team_roles?: string[];
    max_turns?: number;
    timeout_minutes?: number;
    require_plan_approval?: boolean;
    max_team_size?: number;
    max_parallel?: number;
    allow_subagents?: boolean;
    instructions?: string | null;
    plan?: { summary?: string; rationale?: string };
    plan_warnings?: string[];
    remembered_approvals?: Array<{ tool: string; command?: string }>;
    scope?: Record<string, { name: string; description: string }>;
  };
  const usage = run.usage as Record<string, number | undefined>;
  const workflow = runWorkflow(run);
  const titles = new Map(run.subtasks.map((s) => [s.key, s.title]));
  const agentName = (key: string) => (workflow && config.scope?.[key]?.name) || key;

  return (
    <div className="mx-auto grid max-w-[1180px] gap-12 px-8 py-8 lg:grid-cols-[1fr_320px]">
      <div className="min-w-0 space-y-10">
        <section>
          <h2 className="mb-3 text-md font-semibold">Result</h2>
          {run.summary ? (
            <div className="rounded-panel bg-panel px-6 py-5">
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
            <h2 className="mb-3 flex items-center gap-2 text-md font-semibold">
              <ListTree className="size-5 text-text-3" /> {workflow ? "Steps" : "Plan"}
            </h2>
            {config.plan?.summary && <Md className="mb-3 text-sm text-text-2">{config.plan.summary}</Md>}
            {config.plan_warnings?.length ? <Notice tone="warn" className="mb-3">{config.plan_warnings.join(" ")}</Notice> : null}
            <ol className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
              {run.subtasks.map((s) => (
                <li key={s.id} className="flex items-start gap-4 px-5 py-4">
                  {!workflow && <span className="mono mt-0.5 text-xs text-text-3">{s.key}</span>}
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-medium">{s.title}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-3">
                      {s.kind === "approval" ? <RoleTag name="Approval by you" color="var(--st-approval)" /> : <RoleTag name={agentName(s.role)} color={roleColor(s.role)} />}
                      {s.depends_on.length > 0 && (
                        <span>after {(workflow ? s.depends_on.map((k) => titles.get(k) ?? k) : s.depends_on).join(", ")}</span>
                      )}
                      {s.attempts > 1 && <span>{s.attempts} attempts</span>}
                    </p>
                    {s.error && <p className={cn("mt-1 text-xs", s.status === "skipped" ? "text-text-3" : "text-st-fail")}>{s.error}</p>}
                  </div>
                  <StatusPill state={subtaskState(s.status)} label={stepStatusLabel(s.status, s.kind)} />
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
      <aside className="space-y-10">
        <section>
          <h2 className="mb-3 text-md font-semibold">Usage</h2>
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
          <h2 className="mb-3 text-md font-semibold">Configuration</h2>
          <KeyValue
            items={[
              ...(workflow
                ? ([
                    ["Workflow", <Link to={`/workflows/${workflow.id}`} className="hover:text-accent-strong">{workflow.name}</Link>],
                    [
                      "Ran",
                      workflow.version ? (
                        <Link to={`/workflows/${workflow.id}/versions/${workflow.version}`} className="hover:text-accent-strong">
                          Version {workflow.version}
                        </Link>
                      ) : (
                        `The draft, revision ${workflow.revision}`
                      ),
                    ],
                    ["Steps", `${plural(workflow.steps.length, "step")}, up to ${config.max_parallel ?? "?"} at a time`],
                    ["Agents", (config.team_roles ?? []).map(agentName).join(", ")],
                  ] as [string, ReactNode][])
                : ([
                    ["Mode", strategyLabel(run)],
                    config.mode === "single" ? ["Role", String(config.single_role)] : ["Roles", (config.team_roles ?? []).join(", ")],
                  ] as [string, ReactNode][])),
              // A workflow step can set its own limits; these apply to the steps that don't.
              [workflow ? "Default max turns" : "Max turns", String(config.max_turns ?? "")],
              [workflow ? "Default time limit" : "Time limit", `${config.timeout_minutes ?? "?"} min per session`],
              ...(config.mode === "team" || config.mode === "auto" ? [["Plan approval", config.require_plan_approval ? "Required" : "Off"] as [string, string]] : []),
              ["Isolation", run.isolation === "worktree" ? "Git worktree" : "In place"],
              ["Created", relativeTime(run.created_at)],
            ]}
          />
          {config.instructions && <p className="mt-3 text-sm text-text-2">Extra instructions: {config.instructions}</p>}
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
