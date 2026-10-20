import type { ExecutionEvent } from "@/api/types";
import { mergeEvents } from "@/stores/events";
import single from "@/test/fixtures/single-run.json";
import team from "@/test/fixtures/team-run.json";
import workflowFailed from "@/test/fixtures/workflow-failed.json";
import workflowRun from "@/test/fixtures/workflow-run.json";

import { DEFAULT_VIEW, reduceEvents, viewGraph, type ViewOptions } from "./reduce";

const teamEvents = team as ExecutionEvent[];
const singleEvents = single as ExecutionEvent[];

function view(overrides: Partial<ViewOptions> = {}): ViewOptions {
  return { ...DEFAULT_VIEW, expanded: new Set(), collapsed: new Set(), hidden: new Set(), ...overrides };
}

describe("reduceEvents — team run (recorded from the backend)", () => {
  const model = reduceEvents(teamEvents);
  const kinds = [...model.nodes.values()].map((n) => n.kind);

  it("reconstructs the real delegation structure", () => {
    expect(kinds.filter((k) => k === "coordinator")).toHaveLength(1);
    expect(kinds.filter((k) => k === "agent")).toHaveLength(4);
    expect(kinds.filter((k) => k === "subtask")).toHaveLength(4);
    const planned = [...model.edges.values()].filter((e) => e.kind === "planned");
    expect(planned).toHaveLength(4);
    const assigned = [...model.edges.values()].filter((e) => e.kind === "assigned" && e.source.startsWith("subtask:"));
    expect(assigned).toHaveLength(4);
  });

  it("captures subtask dependencies and routed messages", () => {
    const deps = [...model.edges.values()].filter((e) => e.kind === "depends");
    expect(deps.length).toBe(3); // s1→s2, s2→s3, s2→s4
    const messages = [...model.edges.values()].filter((e) => e.kind === "message");
    expect(messages.some((m) => m.label === "report")).toBe(true);
    // Assignments travel plan → subtask → agent; they are kept in the agent's inbox, not drawn twice.
    expect(messages.some((m) => m.label === "assignment")).toBe(false);
    const agents = [...model.nodes.values()].filter((n) => n.kind === "agent");
    expect(agents.every((a) => (a.data.inbox as Array<{ kind: string }>).some((m) => m.kind === "assignment"))).toBe(true);
  });

  it("ends in a successful outcome with every agent finished", () => {
    expect(model.meta.runStatus).toBe("completed");
    expect(model.nodes.get("outcome")?.state).toBe("success");
    for (const n of model.nodes.values()) {
      if (n.kind === "agent" || n.kind === "coordinator" || n.kind === "subtask") expect(n.state).toBe("success");
    }
    for (const call of model.tools.values()) expect(call.state).not.toBe("running");
  });

  it("builds one timeline lane per agent with tool spans", () => {
    expect(model.lanes.size).toBe(5);
    const spans = [...model.lanes.values()].flatMap((l) => l.spans.filter((s) => s.kind === "tool"));
    expect(spans.length).toBe(model.tools.size);
    expect(spans.every((s) => s.end)).toBe(true);
  });

  it("replays history: a prefix of the log is an earlier state", () => {
    const planEvent = teamEvents.find((e) => e.type === "plan.created")!;
    const early = reduceEvents(teamEvents, planEvent.seq);
    expect([...early.nodes.values()].filter((n) => n.kind === "agent")).toHaveLength(0);
    expect([...early.nodes.values()].filter((n) => n.kind === "subtask").every((n) => n.state === "pending")).toBe(true);
    expect(early.nodes.has("outcome")).toBe(false);
    expect(early.lastSeq).toBe(planEvent.seq);
  });

  it("is deterministic regardless of delivery order and duplicates", () => {
    const shuffled = [...teamEvents].reverse();
    const merged = mergeEvents(mergeEvents([], shuffled), teamEvents.slice(0, 20));
    expect(merged.map((e) => e.seq)).toEqual(teamEvents.map((e) => e.seq));
    const again = reduceEvents(merged);
    expect([...again.nodes.keys()].sort()).toEqual([...model.nodes.keys()].sort());
  });
});

describe("viewGraph", () => {
  const model = reduceEvents(teamEvents);

  it("aggregates tool calls per agent by default and expands on demand", () => {
    const aggregated = viewGraph(model, view());
    const groups = aggregated.nodes.filter((n) => n.kind === "toolgroup");
    expect(groups.length).toBeGreaterThan(0);
    expect(aggregated.nodes.some((n) => n.kind === "tool")).toBe(false);
    const agent = groups[0].owner!;
    const expanded = viewGraph(model, view({ expanded: new Set([agent]) }));
    expect(expanded.nodes.filter((n) => n.kind === "tool" && n.owner === agent).length).toBeGreaterThan(0);
  });

  it("collapses an agent subtree", () => {
    const coordinator = [...model.nodes.values()].find((n) => n.kind === "coordinator")!;
    const collapsed = viewGraph(model, view({ collapsed: new Set([coordinator.id]) }));
    expect(collapsed.nodes.some((n) => n.kind === "subtask" || n.kind === "agent")).toBe(false);
    expect(collapsed.nodes.find((n) => n.id === coordinator.id)?.data.collapsedCount).toBeGreaterThan(0);
  });

  it("focuses on one agent with its ancestry", () => {
    const agent = [...model.nodes.values()].find((n) => n.kind === "agent")!;
    const focused = viewGraph(model, view({ focus: agent.id }));
    const ids = new Set(focused.nodes.map((n) => n.id));
    expect(ids.has(agent.id)).toBe(true);
    expect(focused.nodes.some((n) => n.kind === "task")).toBe(true);
    expect(focused.nodes.filter((n) => n.kind === "agent")).toHaveLength(1);
    for (const e of focused.edges) expect(ids.has(e.source) && ids.has(e.target)).toBe(true);
  });

  it("filters event kinds", () => {
    const filtered = viewGraph(model, view({ hidden: new Set(["tools", "files", "messages", "plan"]) }));
    expect(filtered.nodes.some((n) => ["tool", "toolgroup", "file", "subtask"].includes(n.kind))).toBe(false);
    expect(filtered.edges.some((e) => e.kind === "message")).toBe(false);
    // Agents stay connected to the coordinator when the plan layer is hidden.
    expect(filtered.edges.filter((e) => e.kind === "delegated")).toHaveLength(4);
  });
});

describe("reduceEvents — single run with an approval", () => {
  const model = reduceEvents(singleEvents);

  it("shows the approval checkpoint and its resolution", () => {
    const approval = [...model.nodes.values()].find((n) => n.kind === "approval")!;
    expect(approval.state).toBe("success");
    expect(approval.data.toolName).toBe("Bash");
    const lane = [...model.lanes.values()][0];
    expect(lane.spans.some((s) => s.kind === "approval" && s.end)).toBe(true);
  });

  it("records files the agent produced", () => {
    const files = [...model.nodes.values()].filter((n) => n.kind === "file");
    expect(files.length).toBeGreaterThan(0);
    expect([...model.edges.values()].some((e) => e.kind === "produced")).toBe(true);
  });
});

describe("reduceEvents — SDK subagents", () => {
  const base = { run_id: "r", task_id: "t", ts: "2026-10-19T10:00:00Z", status: null } as const;
  let seq = 0;
  const ev = (type: string, extra: Partial<ExecutionEvent>): ExecutionEvent =>
    ({ ...base, seq: ++seq, id: `e${seq}`, session_id: "ses", agent_id: "ses", parent_agent_id: null, type, data: {}, ...extra }) as ExecutionEvent;
  const events = [
    ev("run.created", { session_id: null, agent_id: null, data: { task_key: "BOS-1", task_title: "T", mode: "single" } }),
    ev("session.created", { data: { role: "implementer", name: "Implementer", kind: "agent" } }),
    ev("tool.call", { data: { tool_use_id: "a1", name: "Agent", input: { subagent_type: "researcher", description: "Map" } } }),
    ev("subagent.spawned", { agent_id: "ses/a1", parent_agent_id: "ses", data: { tool_use_id: "a1", subagent_type: "researcher", description: "Map" } }),
    ev("tool.call", { agent_id: "ses/a1", parent_agent_id: "ses", data: { tool_use_id: "g1", name: "Grep", input: { pattern: "x" }, parent_tool_use_id: "a1" } }),
    ev("tool.result", { agent_id: "ses/a1", parent_agent_id: "ses", data: { tool_use_id: "g1", name: "Grep", is_error: false, output: "1 match" } }),
    ev("subagent.returned", { agent_id: "ses", data: { tool_use_id: "a1", output: "found it" }, status: "completed" }),
  ];

  it("draws observed Agent-tool delegation and attributes inner tool calls", () => {
    const model = reduceEvents(events);
    const sub = model.nodes.get("ses/a1")!;
    expect(sub.kind).toBe("subagent");
    expect(sub.state).toBe("success");
    expect([...model.edges.values()].some((e) => e.source === "ses" && e.target === "ses/a1" && e.kind === "delegated")).toBe(true);
    expect(model.tools.get("g1")?.agentId).toBe("ses/a1");
    expect(model.tools.has("a1")).toBe(true);
    const visible = viewGraph(model, view());
    expect(visible.nodes.some((n) => n.kind === "toolgroup" && n.owner === "ses/a1")).toBe(true);
    // The Agent tool call itself is represented by the subagent node, not a tool node.
    expect(visible.nodes.some((n) => n.label === "Agent")).toBe(false);
  });
});

describe("reduceEvents — workflow runs (a plan without a coordinator)", () => {
  const model = reduceEvents(workflowRun as ExecutionEvent[]);
  const nodes = [...model.nodes.values()];
  const edges = [...model.edges.values()];
  const step = (key: string) => nodes.find((n) => n.kind === "subtask" && n.data.key === key)!.id;
  const agentOf = (key: string, m = model) => [...m.edges.values()].find((e) => e.kind === "assigned" && e.source === [...m.nodes.values()].find((n) => n.kind === "subtask" && n.data.key === key)!.id)!.target;
  const task = nodes.find((n) => n.kind === "task")!;

  it("draws each step, its agent and the authored order", () => {
    expect(nodes.filter((n) => n.kind === "coordinator")).toHaveLength(0);
    expect(nodes.filter((n) => n.kind === "subtask")).toHaveLength(5);
    expect(nodes.filter((n) => n.kind === "agent")).toHaveLength(5);
    const deps = edges.filter((e) => e.kind === "depends").map((e) => `${model.nodes.get(e.source)!.data.key}>${model.nodes.get(e.target)!.data.key}`);
    expect(deps.sort()).toEqual(["implement>review", "implement>test", "research>implement", "review>final", "test>final"]);
    expect(edges.some((e) => e.kind === "message" && e.label === "report" && e.source === agentOf("research") && e.target === agentOf("implement"))).toBe(true);
  });

  it("connects the task to the first step and the last step to the outcome", () => {
    expect(edges.filter((e) => e.target === step("research")).map((e) => [e.source, e.kind])).toEqual([[task.id, "planned"]]);
    expect(edges.filter((e) => e.kind === "outcome").map((e) => e.source)).toEqual([agentOf("final")]);
    expect(model.nodes.get("outcome")?.state).toBe("success");
  });

  it("keeps agents attached to the task when the plan is hidden", () => {
    const g = viewGraph(model, view({ hidden: new Set(["plan"]) }));
    const agents = g.nodes.filter((n) => n.kind === "agent");
    expect(agents.every((a) => g.edges.some((e) => e.source === task.id && e.target === a.id))).toBe(true);
  });

  it("shows steps that never ran as skipped, and ends at each branch's last step", () => {
    const failed = reduceEvents(workflowFailed as ExecutionEvent[]);
    const state = (key: string) => [...failed.nodes.values()].find((n) => n.kind === "subtask" && n.data.key === key)!.state;
    expect([state("left"), state("left2"), state("right"), state("right2")]).toEqual(["failed", "skipped", "success", "success"]);
    const left2 = [...failed.nodes.values()].find((n) => n.data.key === "left2")!.id;
    const sources = [...failed.edges.values()].filter((e) => e.kind === "outcome").map((e) => e.source);
    expect(sources.sort()).toEqual([left2, agentOf("right2", failed)].sort());
    expect(failed.nodes.get("outcome")?.state).toBe("failed");
  });

  it("leaves team runs hanging off their coordinator", () => {
    const teamModel = reduceEvents(teamEvents);
    const coordinator = [...teamModel.nodes.values()].find((n) => n.kind === "coordinator")!.id;
    expect([...teamModel.edges.values()].filter((e) => e.kind === "outcome").map((e) => e.source)).toEqual([coordinator]);
    expect([...teamModel.edges.values()].filter((e) => e.kind === "planned").every((e) => e.source === coordinator)).toBe(true);
  });
});
