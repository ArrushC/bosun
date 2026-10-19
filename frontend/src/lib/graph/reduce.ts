/**
 * Reconstruct a run's agent graph and timeline from its execution events.
 *
 * Pure function of the ordered event log: the live view, a reloaded page and
 * historical replay (a prefix of the log) all produce the same model. Only
 * observed facts become nodes and edges — sessions Bosun started,
 * subagents spawned through the Agent tool, tool calls, routed messages,
 * approvals, files written and outcomes. Nothing is inferred or invented.
 */
import type { ExecutionEvent } from "@/api/types";
import { roleColor, runState, sessionState, subtaskState, type VisualState } from "@/lib/status";
import { basename, dirname, summariseToolInput } from "@/lib/utils";

export type NodeKind =
  | "task"
  | "coordinator"
  | "agent"
  | "subagent"
  | "subtask"
  | "tool"
  | "toolgroup"
  | "file"
  | "approval"
  | "outcome"
  | "operator";

export type EdgeKind =
  | "assigned"
  | "delegated"
  | "planned"
  | "depends"
  | "invoked"
  | "produced"
  | "message"
  | "approval"
  | "instruction"
  | "outcome";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  sublabel?: string;
  state: VisualState;
  owner?: string;
  color?: string;
  firstSeq: number;
  lastSeq: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  label?: string;
  active?: boolean;
  seq: number;
}

export interface ToolCall {
  id: string;
  name: string;
  agentId: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  input: Record<string, any>;
  summary: string;
  state: VisualState;
  startTs: string;
  endTs?: string;
  durationMs?: number | null;
  output?: string;
  isError?: boolean;
  denied?: string;
  seqStart: number;
  seqEnd?: number;
}

export interface Span {
  id: string;
  kind: "session" | "tool" | "approval";
  label: string;
  start: string;
  end?: string;
  state: VisualState;
  seq: number;
}

export interface Mark {
  kind: "text" | "message-in" | "message-out" | "user" | "denied" | "file" | "result";
  ts: string;
  seq: number;
  label: string;
  peer?: string;
}

export interface Lane {
  id: string;
  label: string;
  role?: string;
  color: string;
  kind: NodeKind;
  parent?: string;
  start: string;
  end?: string;
  state: VisualState;
  spans: Span[];
  marks: Mark[];
}

export interface RunModel {
  nodes: Map<string, GraphNode>;
  edges: Map<string, GraphEdge>;
  tools: Map<string, ToolCall>;
  lanes: Map<string, Lane>;
  meta: {
    runId?: string;
    taskId?: string;
    taskKey?: string;
    taskTitle?: string;
    runStatus?: string;
    mode?: string;
    branch?: string | null;
    isolation?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rateLimit?: Record<string, any>;
    startTs?: string;
    endTs?: string;
  };
  firstTs?: string;
  lastTs?: string;
  lastSeq: number;
  eventCount: number;
}

const SUBAGENT_TOOLS = new Set(["Agent", "Task"]);

interface PlanItem {
  subtask_id: string;
  key: string;
  title: string;
  role: string;
  instructions: string;
  depends_on: string[];
  modifies_code: boolean;
}

function terminal(state: VisualState) {
  return state === "success" || state === "failed" || state === "cancelled" || state === "paused";
}

export function emptyModel(): RunModel {
  return { nodes: new Map(), edges: new Map(), tools: new Map(), lanes: new Map(), meta: {}, lastSeq: 0, eventCount: 0 };
}

/** Applies events one at a time; the live view feeds it only newly appended events. */
export interface ModelBuilder {
  model: RunModel;
  apply: (e: ExecutionEvent) => void;
  finalize: () => RunModel;
}

export function createBuilder(): ModelBuilder {
  const m = emptyModel();
  const subtaskByKey = new Map<string, string>();
  const subagentByTool = new Map<string, string>();

  const node = (n: Omit<GraphNode, "firstSeq" | "lastSeq"> & { seq: number }) => {
    const existing = m.nodes.get(n.id);
    if (existing) {
      Object.assign(existing, { ...n, data: { ...existing.data, ...n.data }, lastSeq: n.seq });
      return existing;
    }
    const created: GraphNode = { ...n, firstSeq: n.seq, lastSeq: n.seq };
    delete (created as Partial<{ seq: number }>).seq;
    m.nodes.set(n.id, created);
    return created;
  };
  const update = (id: string, patch: Partial<GraphNode>, seq: number) => {
    const n = m.nodes.get(id);
    if (!n) return;
    Object.assign(n, patch, { data: { ...n.data, ...(patch.data ?? {}) }, lastSeq: seq });
  };
  const edge = (e: GraphEdge) => {
    if (e.source === e.target) return;
    const existing = m.edges.get(e.id);
    m.edges.set(e.id, existing ? { ...existing, ...e } : e);
  };
  const lane = (id: string) => m.lanes.get(id);
  const taskNodeId = () => (m.meta.taskId ? `task:${m.meta.taskId}` : "task");

  const spans = new Map<string, Span>();
  const findSpan = (id: string) => spans.get(id);

  const apply = (e: ExecutionEvent) => {
    m.lastSeq = e.seq;
    m.eventCount += 1;
    m.firstTs ??= e.ts;
    m.lastTs = e.ts;
    const d = e.data ?? {};
    const agentId = e.agent_id ?? e.session_id ?? undefined;

    switch (e.type) {
      case "run.created": {
        m.meta = { ...m.meta, runId: e.run_id, taskId: e.task_id ?? undefined, taskKey: d.task_key, taskTitle: d.task_title, mode: d.mode, runStatus: "queued", startTs: e.ts };
        node({ id: taskNodeId(), kind: "task", label: d.task_title ?? "Task", sublabel: d.task_key, state: "pending", seq: e.seq, data: { mode: d.mode, backend: d.backend, provider: d.provider, model: d.model } });
        break;
      }
      case "run.status": {
        m.meta.runStatus = d.to;
        update(taskNodeId(), { state: runState(d.to) }, e.seq);
        break;
      }
      case "workspace.prepared":
        m.meta.branch = d.branch;
        m.meta.isolation = d.isolation;
        update(taskNodeId(), { data: { branch: d.branch, isolation: d.isolation, base_branch: d.base_branch } }, e.seq);
        break;
      case "session.created":
      case "session.resumed": {
        if (!e.session_id) break;
        const isCoordinator = d.kind === "coordinator";
        const existing = m.nodes.get(e.session_id);
        const color = roleColor(d.role, d.color);
        node({
          id: e.session_id,
          kind: isCoordinator ? "coordinator" : "agent",
          label: (d.name as string | undefined)?.split(": ")[0] ?? d.role_name ?? d.role,
          sublabel: d.subtask_id ? (d.name as string).split(": ").slice(1).join(": ") : d.role_name ?? d.role,
          state: "pending",
          color,
          seq: e.seq,
          data: { role: d.role, kind: d.kind, cwd: d.cwd, branch: d.branch, attempt: d.attempt, profile: d.permission_profile, prompt: existing?.data.prompt ?? d.prompt, subtaskId: d.subtask_id, turns: (existing?.data.turns ?? 0) + 1 },
        });
        if (d.subtask_id) {
          edge({ id: `assign:${d.subtask_id}:${e.session_id}`, source: `subtask:${d.subtask_id}`, target: e.session_id, kind: "assigned", seq: e.seq });
        } else if (e.parent_agent_id) {
          edge({ id: `deleg:${e.parent_agent_id}:${e.session_id}`, source: e.parent_agent_id, target: e.session_id, kind: "delegated", seq: e.seq });
        } else {
          edge({ id: `assign:${e.session_id}`, source: taskNodeId(), target: e.session_id, kind: "assigned", seq: e.seq });
        }
        if (!m.lanes.has(e.session_id)) {
          m.lanes.set(e.session_id, {
            id: e.session_id,
            label: (d.name as string | undefined) ?? d.role,
            role: d.role,
            color,
            kind: isCoordinator ? "coordinator" : "agent",
            start: e.ts,
            state: "pending",
            spans: [],
            marks: [],
          });
        }
        lane(e.session_id)?.spans.push({ id: `${e.session_id}:turn:${e.seq}`, kind: "session", label: e.type === "session.resumed" ? "Resumed" : "Session", start: e.ts, state: "running", seq: e.seq });
        break;
      }
      case "session.status": {
        if (!e.session_id) break;
        const state = sessionState(e.status);
        update(e.session_id, { state }, e.seq);
        const l = lane(e.session_id);
        if (l) l.state = state;
        break;
      }
      case "session.init": {
        if (e.session_id) update(e.session_id, { data: { model: d.model, sdkSessionId: d.sdk_session_id, apiKeySource: d.api_key_source, tools: d.tools, claudeCodeVersion: d.claude_code_version } }, e.seq);
        break;
      }
      case "session.completed":
      case "session.failed":
      case "session.interrupted":
      case "session.cancelled": {
        if (!e.session_id) break;
        const state = sessionState(e.status);
        update(e.session_id, { state, data: { result: d.result, error: d.error, errorKind: d.error_kind, usage: d.usage, durationMs: d.duration_ms, numTurns: d.num_turns } }, e.seq);
        const l = lane(e.session_id);
        if (l) {
          l.state = state;
          l.end = e.ts;
          const open = [...l.spans].reverse().find((s) => s.kind === "session" && !s.end);
          if (open) {
            open.end = e.ts;
            open.state = state;
          }
          l.marks.push({ kind: "result", ts: e.ts, seq: e.seq, label: d.error ? `Failed: ${d.error}` : "Finished" });
        }
        break;
      }
      case "assistant.text": {
        const target = agentId;
        if (!target) break;
        update(target, { data: { lastText: d.text, lastTextSeq: e.seq } }, e.seq);
        lane(target)?.marks.push({ kind: "text", ts: e.ts, seq: e.seq, label: String(d.text ?? "").slice(0, 280) });
        break;
      }
      case "user.message": {
        const target = e.session_id;
        if (!target) break;
        node({ id: "operator", kind: "operator", label: "You", sublabel: "Operator", state: "success", seq: e.seq, data: {} });
        edge({ id: `instr:${target}`, source: "operator", target, kind: "instruction", label: "instruction", seq: e.seq });
        lane(target)?.marks.push({ kind: "user", ts: e.ts, seq: e.seq, label: String(d.text ?? "") });
        const n = m.nodes.get(target);
        if (n) (n.data.instructions ??= []).push({ text: d.text, ts: e.ts, queued: d.queued });
        break;
      }
      case "tool.call": {
        if (!agentId || SUBAGENT_TOOLS.has(d.name)) {
          if (agentId && SUBAGENT_TOOLS.has(d.name)) {
            m.tools.set(d.tool_use_id, { id: d.tool_use_id, name: d.name, agentId, input: d.input ?? {}, summary: summariseToolInput(d.name, d.input), state: "running", startTs: e.ts, seqStart: e.seq });
          }
          break;
        }
        const call: ToolCall = { id: d.tool_use_id, name: d.name, agentId, input: d.input ?? {}, summary: summariseToolInput(d.name, d.input), state: "running", startTs: e.ts, seqStart: e.seq };
        m.tools.set(call.id, call);
        const span: Span = { id: call.id, kind: "tool", label: `${call.name} ${call.summary}`.trim(), start: e.ts, state: "running", seq: e.seq };
        spans.set(call.id, span);
        lane(agentId)?.spans.push(span);
        break;
      }
      case "tool.result": {
        const call = m.tools.get(d.tool_use_id);
        if (!call) break;
        call.state = call.denied ? "failed" : d.is_error ? "failed" : "success";
        call.endTs = e.ts;
        call.durationMs = d.duration_ms;
        call.output = d.output;
        call.isError = d.is_error;
        call.seqEnd = e.seq;
        const span = findSpan(call.id);
        if (span) {
          span.end = e.ts;
          span.state = call.state;
        }
        break;
      }
      case "tool.denied": {
        const call = d.tool_use_id ? m.tools.get(d.tool_use_id) : undefined;
        if (call) {
          call.denied = d.reason;
          call.state = "failed";
        }
        if (agentId) lane(agentId)?.marks.push({ kind: "denied", ts: e.ts, seq: e.seq, label: d.reason ?? `${d.tool} denied` });
        break;
      }
      case "subagent.spawned": {
        if (!e.agent_id) break;
        subagentByTool.set(d.tool_use_id, e.agent_id);
        const parent = e.parent_agent_id ?? e.session_id ?? undefined;
        const color = roleColor(d.subagent_type);
        node({ id: e.agent_id, kind: "subagent", label: d.subagent_type ?? "subagent", sublabel: d.description, state: "running", owner: parent, color, seq: e.seq, data: { prompt: d.prompt, background: d.background, toolUseId: d.tool_use_id } });
        if (parent) edge({ id: `deleg:${parent}:${e.agent_id}`, source: parent, target: e.agent_id, kind: "delegated", label: "Agent tool", seq: e.seq });
        m.lanes.set(e.agent_id, { id: e.agent_id, label: `${d.subagent_type ?? "subagent"} (subagent)`, role: d.subagent_type, color, kind: "subagent", parent, start: e.ts, state: "running", spans: [], marks: [] });
        break;
      }
      case "subagent.returned":
      case "task.finished": {
        const id = subagentByTool.get(d.tool_use_id);
        if (!id) break;
        const state: VisualState = e.status === "failed" ? "failed" : e.status === "stopped" || e.status === "killed" ? "cancelled" : "success";
        const n = m.nodes.get(id);
        if (n && !terminal(n.state)) update(id, { state, data: { output: d.output ?? d.summary } }, e.seq);
        else if (n && d.output) update(id, { data: { output: d.output } }, e.seq);
        const l = lane(id);
        if (l && !l.end) {
          l.end = e.ts;
          l.state = state;
        }
        break;
      }
      case "task.started": {
        const id = subagentByTool.get(d.tool_use_id);
        if (id) update(id, { data: { taskId: d.task_id, background: true } }, e.seq);
        break;
      }
      case "task.progress": {
        const id = subagentByTool.get(d.tool_use_id);
        if (id) update(id, { data: { lastTool: d.last_tool, usage: d.usage } }, e.seq);
        break;
      }
      case "artifact.file": {
        if (!agentId) break;
        const path = String(d.path ?? "");
        const id = `file:${path}`;
        const existing = m.nodes.get(id);
        node({ id, kind: "file", label: basename(path), sublabel: dirname(path) || "./", state: "success", seq: e.seq, data: { path, writers: [...new Set([...(existing?.data.writers ?? []), agentId])], action: d.action } });
        edge({ id: `prod:${agentId}:${id}`, source: agentId, target: id, kind: "produced", seq: e.seq });
        lane(agentId)?.marks.push({ kind: "file", ts: e.ts, seq: e.seq, label: path });
        break;
      }
      case "artifact.commit": {
        update(taskNodeId(), { data: { commits: [...(m.nodes.get(taskNodeId())?.data.commits ?? []), d.commit].filter(Boolean) } }, e.seq);
        break;
      }
      case "approval.requested": {
        const id = `approval:${d.approval_id}`;
        const owner = e.session_id ?? undefined;
        node({ id, kind: "approval", label: d.title, sublabel: d.kind === "plan" ? "Plan approval" : d.kind === "integration" ? "Merge decision" : d.tool_name, state: "waiting_approval", owner, seq: e.seq, data: { approvalId: d.approval_id, kind: d.kind, toolName: d.tool_name, toolInput: d.tool_input, description: d.description, toolUseId: d.tool_use_id, requestedAt: e.ts } });
        const source = owner ?? (d.kind === "plan" ? findCoordinator(m) : undefined) ?? taskNodeId();
        edge({ id: `appr:${id}`, source, target: id, kind: "approval", seq: e.seq });
        const l = owner ? lane(owner) : undefined;
        const span: Span = { id, kind: "approval", label: d.title, start: e.ts, state: "waiting_approval", seq: e.seq };
        spans.set(id, span);
        l?.spans.push(span);
        if (d.tool_use_id) {
          const call = m.tools.get(d.tool_use_id);
          if (call) call.state = "waiting_approval";
        }
        break;
      }
      case "approval.resolved": {
        const id = `approval:${d.approval_id}`;
        const state: VisualState = e.status === "approved" ? "success" : e.status === "denied" ? "failed" : "cancelled";
        update(id, { state, data: { resolution: d.decision, message: d.message, remember: d.remember, resolvedAt: e.ts, status: e.status } }, e.seq);
        const span = findSpan(id);
        if (span) {
          span.end = e.ts;
          span.state = state;
        }
        if (d.tool_use_id) {
          const call = m.tools.get(d.tool_use_id);
          if (call && call.state === "waiting_approval") call.state = state === "success" ? "running" : "failed";
        }
        break;
      }
      case "approval.auto":
        break;
      case "plan.created": {
        const coordinator = e.session_id ?? findCoordinator(m);
        for (const s of (d.subtasks ?? []) as PlanItem[]) {
          const id = `subtask:${s.subtask_id}`;
          subtaskByKey.set(s.key, id);
          node({ id, kind: "subtask", label: s.title, sublabel: s.key, state: "pending", color: roleColor(s.role), seq: e.seq, data: { key: s.key, role: s.role, instructions: s.instructions, modifiesCode: s.modifies_code, dependsOn: s.depends_on } });
          if (coordinator) edge({ id: `plan:${id}`, source: coordinator, target: id, kind: "planned", seq: e.seq });
        }
        for (const s of (d.subtasks ?? []) as PlanItem[]) {
          for (const dep of s.depends_on ?? []) {
            const from = subtaskByKey.get(dep);
            if (from) edge({ id: `dep:${from}:${s.subtask_id}`, source: from, target: `subtask:${s.subtask_id}`, kind: "depends", seq: e.seq });
          }
        }
        update(taskNodeId(), { data: { plan: { summary: d.summary, rationale: d.rationale, strategy: d.strategy, warnings: d.warnings } } }, e.seq);
        break;
      }
      case "subtask.status": {
        const id = `subtask:${d.subtask_id}`;
        update(id, { state: subtaskState(d.to), data: { attempt: d.attempt, error: d.error, status: d.to } }, e.seq);
        break;
      }
      case "subtask.retry":
        update(`subtask:${d.subtask_id}`, { data: { retries: (m.nodes.get(`subtask:${d.subtask_id}`)?.data.retries ?? 0) + 1, lastError: d.error } }, e.seq);
        break;
      case "agent.message": {
        const from = d.from_session_id as string | null;
        const to = d.to_session_id as string;
        if (!to) break;
        const source = from ?? taskNodeId();
        // Assignments to plan subtasks are already drawn as plan → subtask → agent.
        const viaPlan = d.kind === "assignment" && m.nodes.get(to)?.data.subtaskId;
        if (!viaPlan) edge({ id: `msg:${source}:${to}:${d.kind}`, source, target: to, kind: "message", label: d.kind, seq: e.seq });
        lane(to)?.marks.push({ kind: "message-in", ts: e.ts, seq: e.seq, label: `${d.kind} from ${m.nodes.get(source)?.label ?? "Bosun"}`, peer: source });
        if (from) lane(from)?.marks.push({ kind: "message-out", ts: e.ts, seq: e.seq, label: `${d.kind} to ${m.nodes.get(to)?.label ?? "agent"}`, peer: to });
        const n = m.nodes.get(to);
        if (n) (n.data.inbox ??= []).push({ from: source, kind: d.kind, text: d.text, subtask: d.subtask, seq: e.seq });
        break;
      }
      case "rate_limit":
        m.meta.rateLimit = d;
        break;
      default: {
        if (e.type.startsWith("run.") && e.type !== "run.status" && e.type !== "run.created") {
          const kind = e.type.slice(4);
          if (["completed", "failed", "cancelled", "paused", "interrupted"].includes(kind)) {
            m.meta.runStatus = kind;
            m.meta.endTs = e.ts;
            const state = runState(kind);
            node({ id: "outcome", kind: "outcome", label: kind === "completed" ? "Ready for review" : kind[0].toUpperCase() + kind.slice(1), sublabel: d.error ?? d.reason ?? undefined, state, seq: e.seq, data: { summary: d.summary, error: d.error, errorKind: d.error_kind, usage: d.usage, diff: d.diff } });
            const source = findCoordinator(m) ?? lastAgent(m) ?? taskNodeId();
            for (const id of [...m.edges.keys()]) if (id.startsWith("outcome:")) m.edges.delete(id);
            edge({ id: `outcome:${source}`, source, target: "outcome", kind: "outcome", seq: e.seq });
            update(taskNodeId(), { state }, e.seq);
          } else if (kind === "merged" || kind === "discarded") {
            update("outcome", { data: { integration: kind } }, e.seq);
          }
        }
      }
    }
  };

  const finalize = () => {
    // Edges into running work animate.
    for (const e of m.edges.values()) {
      const target = m.nodes.get(e.target);
      e.active = !!target && (target.state === "running" || target.state === "waiting_approval") && e.kind !== "depends";
    }
    return m;
  };

  return { model: m, apply, finalize };
}

export function reduceEvents(events: ExecutionEvent[], upToSeq?: number): RunModel {
  const builder = createBuilder();
  for (const e of events) {
    if (upToSeq !== undefined && e.seq > upToSeq) break;
    builder.apply(e);
  }
  return builder.finalize();
}

function findCoordinator(m: RunModel): string | undefined {
  for (const n of m.nodes.values()) if (n.kind === "coordinator") return n.id;
  return undefined;
}

function lastAgent(m: RunModel): string | undefined {
  let found: string | undefined;
  for (const n of m.nodes.values()) if (n.kind === "agent") found = n.id;
  return found;
}

// ---------------------------------------------------------------------------------------
// View: aggregation, collapse, focus and filters on top of the model.
// ---------------------------------------------------------------------------------------
export type HiddenKind = "tools" | "files" | "messages" | "approvals" | "plan";

export interface ViewOptions {
  aggregateTools: boolean;
  expanded: Set<string>;
  collapsed: Set<string>;
  focus: string | null;
  hidden: Set<HiddenKind>;
  maxToolsPerAgent: number;
}

export const DEFAULT_VIEW: ViewOptions = {
  aggregateTools: true,
  expanded: new Set(),
  collapsed: new Set(),
  focus: null,
  hidden: new Set(),
  maxToolsPerAgent: 30,
};

function groupState(calls: ToolCall[]): VisualState {
  if (calls.some((c) => c.state === "waiting_approval")) return "waiting_approval";
  if (calls.some((c) => c.state === "running")) return "running";
  if (calls[calls.length - 1]?.state === "failed") return "failed";
  return "success";
}

export function viewGraph(model: RunModel, opts: ViewOptions): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();
  const hidden = opts.hidden;

  for (const n of model.nodes.values()) {
    if (hidden.has("files") && n.kind === "file") continue;
    if (hidden.has("approvals") && n.kind === "approval") continue;
    if (hidden.has("plan") && n.kind === "subtask") continue;
    nodes.set(n.id, n);
  }
  for (const e of model.edges.values()) {
    if (hidden.has("messages") && (e.kind === "message" || e.kind === "instruction")) continue;
    if (hidden.has("plan") && (e.kind === "depends" || e.kind === "planned")) continue;
    edges.set(e.id, e);
  }
  if (hidden.has("plan")) {
    // Re-attach agents that were assigned through a (now hidden) subtask to their coordinator.
    for (const n of model.nodes.values()) {
      if (n.kind === "agent" && n.data.subtaskId) {
        const coordinator = findCoordinator(model);
        if (coordinator) edges.set(`deleg:${coordinator}:${n.id}`, { id: `deleg:${coordinator}:${n.id}`, source: coordinator, target: n.id, kind: "delegated", seq: n.firstSeq, active: n.state === "running" });
      }
    }
  }

  if (!hidden.has("tools")) {
    const byAgent = new Map<string, ToolCall[]>();
    for (const call of model.tools.values()) {
      if (SUBAGENT_TOOLS.has(call.name)) continue;
      const list = byAgent.get(call.agentId) ?? [];
      list.push(call);
      byAgent.set(call.agentId, list);
    }
    for (const [agent, calls] of byAgent) {
      if (!nodes.has(agent)) continue;
      const individual = !opts.aggregateTools || opts.expanded.has(agent);
      if (individual) {
        const shown = calls.slice(-opts.maxToolsPerAgent);
        if (calls.length > shown.length) {
          const older = calls.slice(0, calls.length - shown.length);
          const id = `tg:${agent}:earlier`;
          nodes.set(id, { id, kind: "toolgroup", label: `${older.length} earlier calls`, sublabel: "collapsed", state: groupState(older), owner: agent, firstSeq: older[0].seqStart, lastSeq: older[older.length - 1].seqStart, data: { calls: older } });
          edges.set(`inv:${id}`, { id: `inv:${id}`, source: agent, target: id, kind: "invoked", seq: older[0].seqStart });
        }
        for (const call of shown) {
          const id = `tool:${call.id}`;
          nodes.set(id, { id, kind: "tool", label: call.name, sublabel: call.summary, state: call.state, owner: agent, firstSeq: call.seqStart, lastSeq: call.seqEnd ?? call.seqStart, data: { call } });
          edges.set(`inv:${id}`, { id: `inv:${id}`, source: agent, target: id, kind: "invoked", seq: call.seqStart, active: call.state === "running" || call.state === "waiting_approval" });
        }
      } else {
        const byName = new Map<string, ToolCall[]>();
        for (const call of calls) {
          const group = byName.get(call.name);
          if (group) group.push(call);
          else byName.set(call.name, [call]);
        }
        for (const [name, group] of byName) {
          const id = `tg:${agent}:${name}`;
          const state = groupState(group);
          const failed = group.filter((c) => c.state === "failed").length;
          const last = group[group.length - 1];
          nodes.set(id, {
            id,
            kind: "toolgroup",
            label: name,
            sublabel: failed ? `${failed} failed, latest: ${last.summary}` : last.summary,
            state,
            owner: agent,
            firstSeq: group[0].seqStart,
            lastSeq: last.seqEnd ?? last.seqStart,
            data: { calls: group, count: group.length, failed, last },
          });
          edges.set(`inv:${id}`, { id: `inv:${id}`, source: agent, target: id, kind: "invoked", seq: group[0].seqStart, active: state === "running" || state === "waiting_approval" });
        }
      }
    }
  }

  // Drop edges whose endpoints are not visible.
  for (const [id, e] of edges) if (!nodes.has(e.source) || !nodes.has(e.target)) edges.delete(id);

  // Collapse: hide everything an agent owns or delegated (descendants), keep the agent.
  const children = new Map<string, string[]>();
  for (const e of edges.values()) {
    if (e.kind === "message" || e.kind === "depends" || e.kind === "instruction" || e.kind === "outcome") continue;
    const list = children.get(e.source);
    if (list) list.push(e.target);
    else children.set(e.source, [e.target]);
  }
  const descendants = (root: string) => {
    const out = new Set<string>();
    const stack = [...(children.get(root) ?? [])];
    while (stack.length) {
      const id = stack.pop()!;
      if (out.has(id) || id === root) continue;
      out.add(id);
      stack.push(...(children.get(id) ?? []));
    }
    return out;
  };
  for (const agent of opts.collapsed) {
    if (!nodes.has(agent)) continue;
    const hiddenIds = descendants(agent);
    const n = nodes.get(agent)!;
    nodes.set(agent, { ...n, data: { ...n.data, collapsedCount: hiddenIds.size } });
    for (const id of hiddenIds) nodes.delete(id);
  }

  // Focus: the focused node, its ancestry back to the task, and its descendants.
  if (opts.focus && nodes.has(opts.focus)) {
    const parents = new Map<string, string[]>();
    for (const e of edges.values()) {
      if (e.kind === "message" || e.kind === "depends" || e.kind === "instruction") continue;
      const list = parents.get(e.target);
      if (list) list.push(e.source);
      else parents.set(e.target, [e.source]);
    }
    const keep = new Set<string>([opts.focus, ...descendants(opts.focus)]);
    const stack = [opts.focus];
    while (stack.length) {
      const id = stack.pop()!;
      for (const p of parents.get(id) ?? []) {
        if (!keep.has(p)) {
          keep.add(p);
          stack.push(p);
        }
      }
    }
    for (const id of [...nodes.keys()]) if (!keep.has(id)) nodes.delete(id);
  }

  for (const [id, e] of edges) if (!nodes.has(e.source) || !nodes.has(e.target)) edges.delete(id);
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}
