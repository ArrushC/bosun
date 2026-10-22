import { Bot, ChevronRight, ScrollText, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { Run } from "@/api/types";
import { ApprovalCard, StepContextView } from "@/components/run/ApprovalCard";
import { Button } from "@/components/ui/button";
import { Md, StatusPill } from "@/components/ui/display";
import { Segmented } from "@/components/ui/inputs";
import { type StepStatus, WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { type AgentInfo, agentColor, APPROVAL_COLOR, ApprovalIcon } from "@/components/workflow/parts";
import { PROFILE_LABEL, sessionState, STATE } from "@/lib/status";
import { defaultStep, type StepRow, stepRows } from "@/lib/steps";
import { cn, elapsed, formatDuration, relativeTime } from "@/lib/utils";
import { useMediaQuery } from "@/lib/useMediaQuery";

/** The agents a run froze, as its scope describes them. */
function runAgents(run: Run): AgentInfo[] {
  const scope = (run.config as { scope?: Record<string, { name: string; profile: string }> }).scope ?? {};
  return Object.entries(scope).map(([key, r]) => ({ key, name: r.name, permission_profile: r.profile }));
}

/**
 * The Steps view of a workflow run: its steps as the run defined them, in their authored
 * layout or in execution order, with each one's status and the reason for it. Everything
 * here comes from the run's own record, so it matches the run's status and, once events
 * have caught up, the live graph.
 */
export function RunSteps({ run, selected, onSelect, onOpenTranscript }: { run: Run; selected: string | null; onSelect: (key: string | null) => void; onOpenTranscript: (sessionId: string) => void }) {
  const rows = useMemo(() => stepRows(run), [run]);
  const agents = useMemo(() => runAgents(run), [run]);
  const status = useMemo(() => new Map<string, StepStatus>(rows.map((r) => [r.step.key, { state: r.state, label: r.label }])), [rows]);
  const narrow = useMediaQuery("(max-width: 1180px)");
  const [view, setView] = useState<"graph" | "list">("graph");
  const [panelOpen, setPanelOpen] = useState(true);
  // Until a step is chosen, show the one that needs attention (it changes as the run goes on).
  const [following, setFollowing] = useState(true);
  const current = selected ?? (following ? defaultStep(rows) : null);

  const row = rows.find((r) => r.step.key === current) ?? null;
  const showPanel = !narrow || panelOpen;
  const pick = (key: string | null) => {
    onSelect(key);
    if (key) setPanelOpen(true);
    else setFollowing(false);
  };

  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden">
      <section aria-label="Steps" className="relative min-w-0 flex-1">
        <div className="absolute top-4 left-4 z-10">
          <Segmented
            value={view}
            onChange={setView}
            options={[
              { value: "graph", label: "Graph" },
              { value: "list", label: "List" },
            ]}
          />
        </div>
        {view === "graph" ? (
          <WorkflowCanvas
            steps={rows.map((r) => r.step)}
            agents={[]}
            allRoles={agents}
            issues={[]}
            selected={current}
            onSelect={pick}
            onChange={() => {}}
            onAdd={() => {}}
            addMenuOpen={false}
            onAddMenuOpenChange={() => {}}
            readOnly
            status={status}
            panelHidden={!showPanel}
            coveredRight={narrow && showPanel ? 384 : 0}
            onShowPanel={() => setPanelOpen(true)}
          />
        ) : (
          <StepList rows={rows} agents={agents} selected={current} onSelect={pick} />
        )}
      </section>
      {showPanel && row && <StepPanel run={run} row={row} rows={rows} agents={agents} overlay={narrow} onClose={() => (narrow ? setPanelOpen(false) : pick(null))} onOpenTranscript={onOpenTranscript} />}
      {showPanel && !row && !narrow && <RunStepsSummary rows={rows} />}
    </div>
  );
}

function StepList({ rows, agents, selected, onSelect }: { rows: StepRow[]; agents: AgentInfo[]; selected: string | null; onSelect: (key: string) => void }) {
  const title = (key: string) => rows.find((r) => r.step.key === key)?.step.title.trim() || "Untitled step";
  return (
    <div className="h-full overflow-y-auto px-6 pt-18 pb-8">
      <ol className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel" aria-label="Steps in the order they run">
        {rows.map((r, i) => {
          const approval = r.step.kind === "approval";
          const agent = agents.find((a) => a.key === r.step.agent);
          const attempts = r.subtask && r.subtask.attempts > 1 ? `${r.subtask.attempts} attempts` : null;
          const last = r.sessions[r.sessions.length - 1];
          return (
            <li key={r.step.key}>
              <button
                onClick={() => onSelect(r.step.key)}
                aria-current={selected === r.step.key ? "true" : undefined}
                className={cn("flex w-full items-start gap-4 px-5 py-4 text-left", selected === r.step.key ? "bg-accent-soft" : "hover:bg-raised")}
              >
                <span className="w-6 shrink-0 pt-0.5 text-sm text-text-3 tabular-nums">{i + 1}</span>
                {approval ? <ApprovalIcon className="mt-0.5 size-4 shrink-0" style={{ color: APPROVAL_COLOR }} /> : <Bot className="mt-0.5 size-4 shrink-0" style={{ color: agentColor(agent) }} />}
                <span className="min-w-0 flex-1">
                  <span className="block text-base font-medium">{r.step.title.trim() || "Untitled step"}</span>
                  <span className="mt-0.5 block text-sm text-text-2">
                    {approval ? "Approval by you" : (agent?.name ?? r.step.agent)}
                    {r.step.after.length ? `, after ${r.step.after.map(title).join(" and ")}` : ", starts the run"}
                  </span>
                  {r.reason && <span className={cn("mt-1 block text-sm", r.state === "failed" ? "text-st-fail" : "text-text-3")}>{r.reason}</span>}
                </span>
                <span className="hidden w-28 shrink-0 pt-0.5 text-right text-xs text-text-3 md:block">
                  {[attempts, last?.started_at ? formatDuration(elapsed(last.started_at, last.ended_at)) : null].filter(Boolean).join(", ")}
                </span>
                <StatusPill state={r.state} label={r.label} />
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Panel({ label, overlay, onClose, children }: { label: string; overlay: boolean; onClose: () => void; children: React.ReactNode }) {
  return (
    <aside
      aria-label={label}
      className={cn(
        "flex w-[400px] shrink-0 flex-col overflow-y-auto border-l border-line-soft bg-panel",
        overlay && "absolute top-3 right-3 bottom-3 z-20 w-[min(380px,calc(100%-24px))] rounded-panel border shadow-pop",
      )}
    >
      <Button variant="ghost" size="icon-sm" className="absolute top-4 right-4" onClick={onClose} aria-label="Close the step details">
        <X className="size-4" />
      </Button>
      {children}
    </aside>
  );
}

function RunStepsSummary({ rows }: { rows: StepRow[] }) {
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.label, (counts.get(r.label) ?? 0) + 1);
  return (
    <aside aria-label="Steps summary" className="flex w-[400px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-line-soft bg-panel px-6 py-5">
      <h2 className="text-md font-semibold">Steps</h2>
      <ul className="space-y-2 text-sm">
        {[...counts].map(([label, n]) => (
          <li key={label} className="flex justify-between">
            <span className="text-text-2">{label}</span>
            <span className="tabular-nums">{n}</span>
          </li>
        ))}
      </ul>
      <p className="text-sm text-text-3">Select a step to see its inputs, its report, its attempts and their transcripts.</p>
    </aside>
  );
}

function StepPanel({
  run,
  row,
  rows,
  agents,
  overlay,
  onClose,
  onOpenTranscript,
}: {
  run: Run;
  row: StepRow;
  rows: StepRow[];
  agents: AgentInfo[];
  overlay: boolean;
  onClose: () => void;
  onOpenTranscript: (sessionId: string) => void;
}) {
  const { step, subtask, sessions, approval } = row;
  const isApproval = step.kind === "approval";
  const agent = agents.find((a) => a.key === step.agent);
  const title = (key: string) => rows.find((r) => r.step.key === key);
  const next = rows.filter((r) => r.step.after.includes(step.key));
  const first = sessions[0];
  const last = sessions[sessions.length - 1];
  return (
    <Panel label={`${step.title || "Step"} details`} overlay={overlay} onClose={onClose}>
      <div className="flex items-center gap-3 px-6 pt-5 pb-3 pr-14">
        {isApproval ? <ApprovalIcon className="size-5 shrink-0" style={{ color: APPROVAL_COLOR }} /> : <Bot className="size-5 shrink-0" style={{ color: agentColor(agent) }} />}
        <h2 className="min-w-0 flex-1 truncate text-md font-semibold">{step.title.trim() || "Untitled step"}</h2>
      </div>
      <div className="space-y-6 px-6 pb-8">
        <div className="space-y-2">
          <StatusPill state={row.state} label={row.label} />
          {/* A pending approval's card says it all, including what a paused run means for it. */}
          {row.reason && !(isApproval && approval?.status === "pending") && (
            <p className={cn("text-sm leading-6", row.state === "failed" ? "text-st-fail" : "text-text-2")}>{row.reason}</p>
          )}
        </div>

        {isApproval && approval && approval.status === "pending" && <ApprovalCard approval={approval} />}
        {isApproval && approval && approval.status !== "pending" && (
          <section>
            <h3 className="mb-2 text-sm font-medium">What you were asked to check</h3>
            <StepContextView approval={approval} />
            <p className="mt-3 text-sm text-text-3">
              {approval.status === "approved" ? "Approved" : approval.status === "denied" ? "Rejected" : approval.status === "cancelled" ? "Cancelled" : "Expired"}{" "}
              {relativeTime(approval.resolved_at)}.
            </p>
          </section>
        )}

        <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-3 text-sm">
          <dt className="text-text-3">{isApproval ? "Kind" : "Agent"}</dt>
          <dd className="min-w-0">
            {isApproval ? (
              "Approval by you"
            ) : (
              <>
                {agent?.name ?? step.agent}
                {agent?.permission_profile && <span className="block text-xs text-text-3">{PROFILE_LABEL[agent.permission_profile] ?? agent.permission_profile}</span>}
              </>
            )}
          </dd>
          <dt className="text-text-3">{isApproval ? "Asks after" : "Runs after"}</dt>
          <dd>
            {step.after.length
              ? step.after.map((k) => {
                  const dep = title(k);
                  return (
                    <span key={k} className="mr-3 inline-flex items-center gap-1">
                      {dep?.step.title || k}
                      {dep && <span className="text-xs text-text-3">({dep.label.toLowerCase()})</span>}
                    </span>
                  );
                })
              : "The start of the run"}
          </dd>
          <dt className="text-text-3">Next</dt>
          <dd>{next.length ? next.map((r) => r.step.title).join(", ") : "The result for review"}</dd>
          {!isApproval && subtask && subtask.attempts > 0 && (
            <>
              <dt className="text-text-3">Attempts</dt>
              <dd>
                {subtask.attempts} of {subtask.max_attempts}
              </dd>
            </>
          )}
          {first?.started_at && (
            <>
              <dt className="text-text-3">Started</dt>
              <dd>{relativeTime(first.started_at)}</dd>
              <dt className="text-text-3">Time</dt>
              <dd>{last?.ended_at || row.state === "running" ? formatDuration(elapsed(first.started_at, last?.ended_at)) : "—"}</dd>
            </>
          )}
          {!isApproval && (
            <>
              <dt className="text-text-3">Changes code</dt>
              <dd>{step.changes_code ? (subtask?.merge_commit ? <>Yes, merged as <span className="mono">{subtask.merge_commit.slice(0, 8)}</span></> : "Yes, in its own worktree") : "No"}</dd>
            </>
          )}
        </dl>

        {!isApproval && (
          <>
            <section>
              <h3 className="mb-1.5 text-sm font-medium">Instructions</h3>
              <p className="text-sm leading-6 whitespace-pre-wrap text-text-2">{step.instructions.trim() || "None"}</p>
            </section>
            {first?.prompt && (
              <details className="rounded-[10px] bg-sunken">
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Input: the prompt it was given</summary>
                <pre className="max-h-80 overflow-auto border-t border-line-soft px-3 py-2 font-mono text-xs leading-5 whitespace-pre-wrap text-text-2">{first.prompt}</pre>
              </details>
            )}
            <section>
              <h3 className="mb-1.5 text-sm font-medium">Output: its report</h3>
              {subtask?.report ? (
                <div className="max-h-96 overflow-y-auto rounded-[10px] bg-sunken px-4 py-3">
                  <Md className="text-sm">{subtask.report}</Md>
                </div>
              ) : (
                <p className="text-sm text-text-3">{row.state === "running" ? "Not finished yet." : "No report."}</p>
              )}
            </section>
          </>
        )}

        {sessions.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-sm font-medium">Transcripts</h3>
            <ul className="divide-y divide-line-soft overflow-hidden rounded-[10px] border border-line-soft">
              {sessions.map((s, i) => (
                <li key={s.id}>
                  <button onClick={() => onOpenTranscript(s.id)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-raised">
                    <ScrollText className="size-4 shrink-0 text-text-3" />
                    <span className="min-w-0 flex-1">
                      {sessions.length > 1 ? `Attempt ${s.attempt}${i > 0 && s.attempt === sessions[i - 1].attempt ? ", resumed" : ""}` : "Transcript"}
                      <span className="block text-xs text-text-3">
                        {STATE[sessionState(s.status)].label}
                        {s.error ? `: ${s.error}` : ""}
                      </span>
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-text-3" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {run.status === "completed" && run.isolation === "worktree" && !isApproval && step.changes_code && <p className="text-xs text-text-3">See the Changes tab for everything the run changed.</p>}
      </div>
    </Panel>
  );
}
