import { Bot, Hand, MessageSquareQuote, User } from "lucide-react";
import { useMemo } from "react";

import type { ExecutionEvent, Run } from "@/api/types";
import { ToolCallDetail } from "@/components/graph/Inspector";
import { EmptyState, Md, StatusPill } from "@/components/ui/display";
import { reduceEvents } from "@/lib/graph/reduce";
import { roleColor, sessionState } from "@/lib/status";
import { clockTime, cn } from "@/lib/utils";

/** The observable conversation of one agent session. Hidden reasoning is never recorded. */
export function Transcript({ run, events, sessionId, onSelectSession }: {
  run: Run;
  events: ExecutionEvent[];
  sessionId: string | null;
  onSelectSession: (id: string) => void;
}) {
  const current = sessionId ?? run.sessions[0]?.id ?? null;
  const model = useMemo(() => reduceEvents(events), [events]);
  const items = useMemo(
    () =>
      events.filter(
        (e) =>
          e.session_id === current &&
          ["session.created", "session.resumed", "assistant.text", "tool.call", "user.message", "agent.message", "approval.requested", "approval.resolved", "session.completed", "session.failed", "session.interrupted", "session.notice", "subagent.spawned", "tool.denied"].includes(e.type),
      ),
    [events, current],
  );

  if (!run.sessions.length) return <EmptyState icon={Bot} title="No agent sessions yet">Sessions appear here as soon as an agent starts.</EmptyState>;

  return (
    <div className="flex h-full min-h-0">
      <nav aria-label="Agents" className="w-60 shrink-0 overflow-y-auto border-r border-line-soft p-2">
        {run.sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => onSelectSession(s.id)}
            className={cn("flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-sm hover:bg-raised", current === s.id && "bg-raised")}
          >
            <span className="h-4 w-[3px] shrink-0 rounded-full" style={{ background: roleColor(s.role) }} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{s.name}</span>
              <span className="block text-2xs text-text-3">{s.kind === "coordinator" ? "Coordinator" : s.role}</span>
            </span>
            <StatusPill state={sessionState(s.status)} compact />
          </button>
        ))}
      </nav>
      <ol className="min-w-0 flex-1 space-y-3 overflow-y-auto px-6 py-4">
        {items.map((e) => (
          <TranscriptItem key={e.seq} event={e} toolCall={e.type === "tool.call" ? model.tools.get(e.data.tool_use_id) : undefined} />
        ))}
      </ol>
    </div>
  );
}

function TranscriptItem({ event: e, toolCall }: { event: ExecutionEvent; toolCall?: ReturnType<typeof reduceEvents>["tools"] extends Map<string, infer T> ? T : never }) {
  const time = <span className="text-2xs text-text-3">{clockTime(e.ts)}</span>;
  switch (e.type) {
    case "session.created":
    case "session.resumed":
      return (
        <li className="rounded-[8px] border border-line-soft bg-sunken px-3 py-2">
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold text-text-2">
            {e.type === "session.created" ? "Prompt from Bosun" : "Session resumed"} {time}
          </p>
          {e.data.prompt && (
            <details>
              <summary className="cursor-pointer text-xs text-text-3">Show prompt</summary>
              <pre className="mt-2 max-h-80 overflow-auto font-mono text-[11px] whitespace-pre-wrap text-text-2">{e.data.prompt}</pre>
            </details>
          )}
        </li>
      );
    case "assistant.text":
      return (
        <li className="flex gap-3">
          <Bot className="mt-0.5 size-4 shrink-0 text-text-3" />
          <div className="min-w-0 flex-1">
            {e.data.parent_tool_use_id && <p className="text-2xs text-text-3">inside subagent</p>}
            <Md className="text-[13.5px]">{String(e.data.text)}</Md>
            {time}
          </div>
        </li>
      );
    case "tool.call":
      return toolCall ? (
        <li className="pl-7">
          <ToolCallDetail call={toolCall} />
        </li>
      ) : null;
    case "tool.denied":
      return <li className="pl-7 text-xs text-st-fail">{e.data.reason}</li>;
    case "user.message":
      return (
        <li className="flex gap-3">
          <User className="mt-0.5 size-4 shrink-0 text-accent" />
          <div className={cn("rounded-[8px] border px-3 py-2 text-sm", e.data.undelivered ? "border-dashed border-line" : "border-accent/30")}>
            <p className={cn("whitespace-pre-wrap", e.data.undelivered && "text-text-2")}>{e.data.text}</p>
            <p className="text-2xs text-text-3">
              You
              {e.data.undelivered
                ? ", not delivered: the agent had already stopped. Send it again as a follow-up."
                : e.data.queued
                  ? ", queued until the current step finished"
                  : e.data.follow_up
                    ? ", follow-up"
                    : ""}{" "}
              {clockTime(e.ts)}
            </p>
          </div>
        </li>
      );
    case "agent.message":
      return (
        <li className="flex gap-3">
          <MessageSquareQuote className="mt-0.5 size-4 shrink-0 text-accent" />
          <div className="min-w-0 rounded-[8px] border border-line-soft px-3 py-2">
            <p className="text-2xs text-text-3">
              {e.data.kind === "assignment" ? "Assignment" : "Report"} routed by Bosun{e.data.subtask ? `: ${e.data.subtask}` : ""}
            </p>
            <p className="line-clamp-[12] text-sm whitespace-pre-wrap text-text-2">{e.data.text}</p>
          </div>
        </li>
      );
    case "approval.requested":
    case "approval.resolved":
      return (
        <li className="flex items-center gap-2 pl-7 text-xs text-st-approval">
          <Hand className="size-3.5" />
          {e.type === "approval.requested" ? `Asked for approval: ${e.data.title}` : `Approval ${e.status}${e.data.message ? `: ${e.data.message}` : ""}`}
        </li>
      );
    case "session.notice":
      return <li className="pl-7 text-xs text-text-2">{String(e.data.text)}</li>;
    case "subagent.spawned":
      return (
        <li className="pl-7 text-xs text-text-2">
          Delegated to the <b>{e.data.subagent_type}</b> subagent: {e.data.description}
        </li>
      );
    default:
      return (
        <li className={cn("rounded-[8px] border px-3 py-2 text-sm", e.type === "session.failed" ? "border-st-fail/40" : "border-line-soft")}>
          <p className="flex items-center gap-2 text-xs font-semibold text-text-2">
            {e.type === "session.completed" ? "Session finished" : e.type === "session.failed" ? "Session failed" : "Session interrupted"} {time}
          </p>
          {e.data.error && <p className="text-st-fail">{e.data.error}</p>}
        </li>
      );
  }
}
