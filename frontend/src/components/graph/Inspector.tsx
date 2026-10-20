import { Crosshair, FileCode2, MessagesSquare, RotateCcw, Send, SkipForward, Square, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { useRunAction } from "@/api/hooks";
import type { Run } from "@/api/types";
import { ApprovalCard } from "@/components/run/ApprovalCard";
import { Button } from "@/components/ui/button";
import { KeyValue, Md, StatusPill } from "@/components/ui/display";
import { Checkbox, Textarea } from "@/components/ui/inputs";
import type { GraphNode, RunModel, ToolCall } from "@/lib/graph/reduce";
import { cn, formatDuration, formatTokens, relativeTime } from "@/lib/utils";
import { strategyLabel } from "@/lib/status";

import { TOOL_ICON } from "./nodes";

function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-3 border-t border-line-soft px-6 py-5", className)}>
      <h3 className="text-xs font-semibold text-text-2">{title}</h3>
      {children}
    </section>
  );
}

export function ToolCallDetail({ call, defaultOpen = false }: { call: ToolCall; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const Icon = TOOL_ICON[call.name];
  return (
    <div className="rounded-[10px] border border-line-soft">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-2 py-1.5 text-left" aria-expanded={open}>
        {Icon && <Icon className="size-3.5 shrink-0 text-text-3" />}
        <span className="text-xs font-semibold">{call.name}</span>
        <span className="mono min-w-0 flex-1 truncate text-xs text-text-3">{call.summary}</span>
        <StatusPill state={call.state} compact />
      </button>
      {open && (
        <div className="space-y-2 border-t border-line-soft px-2 py-2">
          {call.denied && <p className="text-xs text-st-fail">{call.denied}</p>}
          <div>
            <p className="mb-1 text-xs text-text-3">Input</p>
            <pre className="max-h-48 overflow-auto rounded-[10px] bg-sunken p-3 font-mono text-xs leading-[18px] whitespace-pre-wrap text-text-2">
              {call.name === "Bash" ? String(call.input.command ?? "") : JSON.stringify(call.input, null, 2)}
            </pre>
          </div>
          {call.output !== undefined && (
            <div>
              <p className="mb-1 text-xs text-text-3">
                Result{call.durationMs ? `, ${formatDuration(call.durationMs)}` : ""}
                {call.isError ? ", error" : ""}
              </p>
              <pre className={cn("max-h-56 overflow-auto rounded-[10px] bg-sunken p-3 font-mono text-xs leading-[18px] whitespace-pre-wrap", call.isError ? "text-st-fail" : "text-text-2")}>
                {call.output || "(empty)"}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Composer({ run, sessionId }: { run: Run; sessionId: string }) {
  const action = useRunAction(run.id);
  const [text, setText] = useState("");
  const [interrupt, setInterrupt] = useState(false);
  const session = run.sessions.find((s) => s.id === sessionId);
  const canSend = run.active ? !!session?.live : !!session?.sdk_session_id && run.status !== "cancelled";
  if (!session) return null;
  const send = () =>
    action.mutate(
      { action: "messages", body: { text, session_id: sessionId, interrupt } },
      { onSuccess: () => setText("") },
    );
  return (
    <Section title="Send an instruction">
      <Textarea
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={run.active ? "Delivered when the agent finishes its current step" : "Continues this agent's session"}
        aria-label="Instruction"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim() && canSend) send();
        }}
      />
      <div className="flex items-center gap-2">
        {run.active && (
          <label className="flex items-center gap-1.5 text-xs text-text-2">
            <Checkbox checked={interrupt} onCheckedChange={setInterrupt} aria-label="Interrupt first" /> Interrupt first
          </label>
        )}
        <Button size="sm" variant="primary" className="ml-auto" disabled={!text.trim() || !canSend || action.isPending} onClick={send}>
          <Send /> {run.active ? "Send" : "Continue session"}
        </Button>
      </div>
      {!canSend && <p className="text-xs text-text-3">{run.active ? "This agent is not running right now." : "This session cannot be continued."}</p>}
    </Section>
  );
}

function AgentDetail({ node, model, run, onFocus }: { node: GraphNode; model: RunModel; run: Run; onFocus: () => void }) {
  const calls = [...model.tools.values()].filter((c) => c.agentId === node.id && c.name !== "Agent" && c.name !== "Task");
  const byName = new Map<string, number>();
  for (const c of calls) byName.set(c.name, (byName.get(c.name) ?? 0) + 1);
  const files = [...model.nodes.values()].filter((n) => n.kind === "file" && (n.data.writers as string[]).includes(node.id));
  const session = run.sessions.find((s) => s.id === node.id);
  const inbox = (node.data.inbox ?? []) as Array<{ from: string; kind: string; text: string; subtask?: string }>;
  const instructions = (node.data.instructions ?? []) as Array<{ text: string; ts: string; queued?: boolean }>;

  return (
    <>
      <div className="flex flex-wrap gap-2 px-6 pb-4">
        <Button size="xs" variant="outline" onClick={onFocus}>
          <Crosshair /> Focus
        </Button>
        {node.kind !== "subagent" && (
          <Button size="xs" variant="outline" asChild>
            <Link to={`?tab=transcript&session=${node.id}`}>
              <MessagesSquare /> Transcript
            </Link>
          </Button>
        )}
      </div>
      <Section title="Session">
        <KeyValue
          items={[
            ["Role", String(node.data.role ?? node.label)],
            ...(node.data.model ? [["Model", <span className="mono">{String(node.data.model)}</span>] as [string, React.ReactNode]] : []),
            ...(session?.branch ? [["Branch", <span className="mono break-all">{session.branch}</span>] as [string, React.ReactNode]] : []),
            ...(session ? [["Turns", `${session.num_turns} model turns, ${session.turn_count} prompt${session.turn_count === 1 ? "" : "s"}`] as [string, React.ReactNode]] : []),
            ...(session?.duration_ms ? [["Active time", formatDuration(session.duration_ms)] as [string, React.ReactNode]] : []),
            ...(session?.usage?.output_tokens ? [["Tokens", `${formatTokens(Number(session.usage.input_tokens ?? 0) + Number(session.usage.cache_read_input_tokens ?? 0))} in, ${formatTokens(Number(session.usage.output_tokens))} out`] as [string, React.ReactNode]] : []),
            ...(node.data.attempt > 1 ? [["Attempt", String(node.data.attempt)] as [string, React.ReactNode]] : []),
            ...(session?.api_key_source ? [["Auth", session.api_key_source === "none" ? "Claude subscription" : session.api_key_source] as [string, React.ReactNode]] : []),
          ]}
        />
        {session?.error && <p className="rounded-[10px] border border-st-fail/40 bg-st-fail/8 px-3 py-2.5 text-sm text-text">{session.error}</p>}
      </Section>
      {node.data.lastText && (
        <Section title="Latest message">
          <Md className="max-h-64 overflow-y-auto text-sm">{String(node.data.lastText)}</Md>
        </Section>
      )}
      {(inbox.length > 0 || instructions.length > 0) && (
        <Section title="Received">
          <ul className="space-y-2">
            {instructions.map((m, i) => (
              <li key={`i${i}`} className="rounded-[10px] border border-accent/30 px-3 py-2.5 text-sm">
                <p className="text-xs text-accent-strong">From you{m.queued ? ", queued" : ""}</p>
                <p className="whitespace-pre-wrap text-text">{m.text}</p>
              </li>
            ))}
            {inbox.map((m, i) => (
              <li key={i} className="rounded-[10px] border border-line-soft px-3 py-2.5 text-sm">
                <p className="text-xs text-text-3">
                  {m.kind === "assignment" ? "Assignment" : "Report"} from {model.nodes.get(m.from)?.label ?? "Bosun"}
                  {m.subtask ? `: ${m.subtask}` : ""}
                </p>
                <p className="line-clamp-6 whitespace-pre-wrap text-text-2">{m.text}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title={`Tool activity (${calls.length})`}>
        {calls.length === 0 ? (
          <p className="text-xs text-text-3">No tool calls yet.</p>
        ) : (
          <>
            <p className="text-xs text-text-2">{[...byName.entries()].map(([n, c]) => `${n} ${c}`).join(", ")}</p>
            <div className="space-y-1">
              {calls.slice(-25).reverse().map((c) => (
                <ToolCallDetail key={c.id} call={c} />
              ))}
            </div>
          </>
        )}
      </Section>
      {files.length > 0 && (
        <Section title="Files written">
          <ul className="space-y-1">
            {files.map((f) => (
              <li key={f.id} className="mono flex items-center gap-1.5 truncate text-xs text-text-2">
                <FileCode2 className="size-3 text-st-ok" /> {String(f.data.path)}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {session?.result && (
        <Section title="Final report">
          <Md className="text-sm">{session.result}</Md>
        </Section>
      )}
      {node.kind !== "subagent" && <Composer key={node.id} run={run} sessionId={node.id} />}
    </>
  );
}

function SubtaskDetail({ node, run }: { node: GraphNode; run: Run }) {
  const action = useRunAction(run.id);
  const subtask = run.subtasks.find((s) => `subtask:${s.id}` === node.id);
  if (!subtask) return null;
  const editable = !run.active && ["failed", "paused", "interrupted"].includes(run.status) && subtask.status !== "completed";
  return (
    <>
      <Section title="Assignment">
        <KeyValue
          items={[
            ["Role", subtask.role],
            ["Depends on", subtask.depends_on.length ? subtask.depends_on.join(", ") : "Nothing"],
            ["Edits code", subtask.modifies_code ? "Yes, in its own worktree" : "No"],
            ["Attempts", `${subtask.attempts} of ${subtask.max_attempts}`],
            ...(subtask.merge_commit ? [["Merged", <span className="mono">{subtask.merge_commit.slice(0, 10)}</span>] as [string, React.ReactNode]] : []),
          ]}
        />
        <Md className="text-sm">{subtask.instructions || "_No instructions._"}</Md>
        {subtask.error && <p className="text-xs text-st-fail">{subtask.error}</p>}
        {editable && (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={action.isPending} onClick={() => action.mutate({ action: `subtasks/${subtask.id}`, body: { action: "retry" } })}>
              <RotateCcw /> Retry
            </Button>
            <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ action: `subtasks/${subtask.id}`, body: { action: "skip" } })}>
              <SkipForward /> Skip
            </Button>
          </div>
        )}
      </Section>
      {subtask.report && (
        <Section title="Report">
          <Md className="text-sm">{subtask.report}</Md>
        </Section>
      )}
    </>
  );
}

export function Inspector({ node, model, run, onClose, onFocus }: { node: GraphNode; model: RunModel; run: Run; onClose: () => void; onFocus: (id: string) => void }) {
  const approval = node.kind === "approval" ? run.approvals.find((a) => `approval:${a.id}` === node.id) : undefined;
  const calls = (node.kind === "toolgroup" ? (node.data.calls as ToolCall[]) : node.kind === "tool" ? [node.data.call as ToolCall] : []) ?? [];

  return (
    <aside aria-label="Inspector" className="flex h-full w-[420px] shrink-0 flex-col border-l border-line-soft bg-panel">
      <header className="flex items-start gap-3 px-6 pt-5 pb-4">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-text-3 capitalize">{node.kind === "toolgroup" ? "Tool calls" : node.kind}</p>
          <h2 className="mt-0.5 truncate text-md font-semibold">{node.label}</h2>
          {node.sublabel && <p className="mt-0.5 truncate text-sm text-text-2">{node.sublabel}</p>}
        </div>
        <StatusPill state={node.state} />
        <button onClick={onClose} className="rounded-control p-1 text-text-3 hover:bg-raised hover:text-text" aria-label="Close inspector">
          <X className="size-4" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto pb-4">
        {(node.kind === "agent" || node.kind === "coordinator" || node.kind === "subagent") && (
          <AgentDetail node={node} model={model} run={run} onFocus={() => onFocus(node.id)} />
        )}
        {node.kind === "subtask" && <SubtaskDetail node={node} run={run} />}
        {calls.length > 0 && (
          <Section title={`${calls.length} call${calls.length === 1 ? "" : "s"}`}>
            <div className="space-y-1">
              {[...calls].reverse().map((c) => (
                <ToolCallDetail key={c.id} call={c} defaultOpen={calls.length === 1} />
              ))}
            </div>
          </Section>
        )}
        {approval && (
          <div className="px-6">
            <ApprovalCard approval={approval} />
          </div>
        )}
        {node.kind === "file" && (
          <Section title="File">
            <p className="mono text-xs break-all">{String(node.data.path)}</p>
            <p className="text-xs text-text-2">Written by {(node.data.writers as string[]).map((w) => model.nodes.get(w)?.label ?? w).join(", ")}</p>
            <Button size="sm" variant="outline" asChild>
              <Link to="?tab=changes">View changes</Link>
            </Button>
          </Section>
        )}
        {node.kind === "task" && (
          <Section title="Run">
            <KeyValue
              items={[
                ["Task", <Link to={`?task=${run.task_id}`} className="hover:text-accent-strong">{run.task_key} {run.task_title}</Link>],
                ["Strategy", strategyLabel(run)],
                ["Branch", run.branch ? <span className="mono break-all">{run.branch}</span> : "Edits in place"],
                ["Started", relativeTime(run.started_at)],
              ]}
            />
            {node.data.plan && (
              <div className="space-y-1">
                <p className="text-xs font-semibold text-text-2">Coordinator's plan</p>
                <Md className="text-sm">{`${node.data.plan.summary ?? ""}\n\n${node.data.plan.rationale ?? ""}`}</Md>
              </div>
            )}
          </Section>
        )}
        {node.kind === "outcome" && (
          <Section title="Outcome">
            {node.data.error && <p className="text-sm text-st-fail">{String(node.data.error)}</p>}
            {node.data.summary ? <Md className="text-sm">{String(node.data.summary)}</Md> : <p className="text-sm text-text-3">No summary.</p>}
          </Section>
        )}
        {node.kind === "operator" && (
          <Section title="Your instructions">
            <p className="text-xs text-text-2">Messages you sent to agents during this run appear on the agents that received them.</p>
          </Section>
        )}
        {run.active && (node.kind === "agent" || node.kind === "coordinator") && node.state === "running" && (
          <div className="px-6 pt-3">
            <p className="flex items-center gap-1.5 text-xs text-text-3">
              <Square className="size-3" /> Use Pause or Cancel in the header to stop the run.
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
