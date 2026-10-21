import dagre from "@dagrejs/dagre";

import type { Run, WorkflowStepIn } from "@/api/types";

/** A workflow step with every field present (the API leaves defaults out). */
export interface Step {
  key: string;
  title: string;
  agent: string | null;
  instructions: string;
  changes_code: boolean;
  after: string[];
  position: { x: number; y: number };
  model: string | null;
  max_turns: number | null;
  timeout_minutes: number | null;
  retries: number | null;
}

export const MAX_STEPS = 20;
export const STEP_SIZE = { width: 232, height: 80 };
const ROW = 140;
const COLUMN = 280;

export function normalizeStep(s: WorkflowStepIn): Step {
  return {
    key: s.key,
    title: s.title ?? "",
    agent: s.agent ?? null,
    instructions: s.instructions ?? "",
    changes_code: s.changes_code ?? false,
    after: s.after ?? [],
    position: s.position ?? { x: 0, y: 0 },
    model: s.model ?? null,
    max_turns: s.max_turns ?? null,
    timeout_minutes: s.timeout_minutes ?? null,
    retries: s.retries ?? null,
  };
}

export function stepTitle(steps: Step[], key: string): string {
  const step = steps.find((s) => s.key === key);
  return step?.title.trim() || "Untitled step";
}

/** Every step `key` waits for, directly or through other steps. */
export function upstream(steps: Step[], key: string): Set<string> {
  const byKey = new Map(steps.map((s) => [s.key, s]));
  const seen = new Set<string>();
  const stack = [...(byKey.get(key)?.after ?? [])];
  while (stack.length) {
    const dep = stack.pop()!;
    if (seen.has(dep)) continue;
    seen.add(dep);
    stack.push(...(byKey.get(dep)?.after ?? []));
  }
  return seen;
}

export function newStepKey(steps: Step[]): string {
  let n = steps.length + 1;
  while (steps.some((s) => s.key === `step-${n}`)) n++;
  return `step-${n}`;
}

/** Add a step after `after` (or at the end), placed below it beside any existing siblings. */
export function addStep(
  steps: Step[],
  fields: { agent: string | null; title: string; changes_code: boolean; after?: string | null },
): { steps: Step[]; key: string } {
  const key = newStepKey(steps);
  const parent = fields.after ? steps.find((s) => s.key === fields.after) : undefined;
  let position: Step["position"];
  if (parent) {
    const siblings = steps.filter((s) => s.after.includes(parent.key)).length;
    position = { x: parent.position.x + siblings * COLUMN, y: parent.position.y + ROW };
  } else {
    const lowest = steps.reduce<Step | undefined>((low, s) => (!low || s.position.y > low.position.y ? s : low), undefined);
    position = lowest ? { x: lowest.position.x, y: lowest.position.y + ROW } : { x: 0, y: 0 };
  }
  const step: Step = {
    key,
    title: fields.title,
    agent: fields.agent,
    instructions: "",
    changes_code: fields.changes_code,
    after: parent ? [parent.key] : [],
    position,
    model: null,
    max_turns: null,
    timeout_minutes: null,
    retries: null,
  };
  return { steps: [...steps, step], key };
}

/** Make `to` run after `from`. Returns the new steps, or why it isn't possible. */
export function connect(steps: Step[], from: string, to: string): Step[] | string {
  if (from === to) return "A step can't run after itself.";
  const target = steps.find((s) => s.key === to);
  if (!target) return steps;
  if (target.after.includes(from)) return steps;
  if (upstream(steps, from).has(to)) {
    return `${stepTitle(steps, from)} already waits for ${stepTitle(steps, to)}, so they would wait for each other.`;
  }
  return steps.map((s) => (s.key === to ? { ...s, after: [...s.after, from] } : s));
}

export function disconnect(steps: Step[], from: string, to: string): Step[] {
  return steps.map((s) => (s.key === to ? { ...s, after: s.after.filter((k) => k !== from) } : s));
}

export function removeSteps(steps: Step[], keys: string[]): Step[] {
  const gone = new Set(keys);
  return steps
    .filter((s) => !gone.has(s.key))
    .map((s) => (s.after.some((k) => gone.has(k)) ? { ...s, after: s.after.filter((k) => !gone.has(k)) } : s));
}

export function updateStep(steps: Step[], key: string, patch: Partial<Step>): Step[] {
  return steps.map((s) => (s.key === key ? { ...s, ...patch } : s));
}

/** Lay the steps out top to bottom (dagre), keeping parallel steps side by side. */
export function tidy(steps: Step[]): Step[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 48, ranksep: 60, marginx: 0, marginy: 0 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const s of steps) g.setNode(s.key, { ...STEP_SIZE });
  for (const s of steps) for (const dep of s.after) if (steps.some((o) => o.key === dep)) g.setEdge(dep, s.key);
  dagre.layout(g);
  return steps.map((s) => {
    const p = g.node(s.key);
    return { ...s, position: { x: Math.round(p.x - STEP_SIZE.width / 2), y: Math.round(p.y - STEP_SIZE.height / 2) } };
  });
}

export interface RunWorkflow {
  id: string;
  name: string;
  revision: number;
  steps: Array<{ key: string; title: string; agent: string | null }>;
}

/** The workflow draft a run executed, frozen when it started (null for other runs). */
export function runWorkflow(run: Pick<Run, "config">): RunWorkflow | null {
  const workflow = (run.config as { workflow?: RunWorkflow | null }).workflow;
  return workflow ?? null;
}
