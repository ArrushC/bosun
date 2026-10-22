import dagre from "@dagrejs/dagre";

import type { Run, WorkflowStepIn } from "@/api/types";

/** A workflow step with every field present (the API leaves defaults out). */
export interface Step {
  key: string;
  /** An agent step runs an agent; an approval step waits for a person to approve or reject. */
  kind: "agent" | "approval";
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

/** A step as stored: drafts and runs saved before approval steps existed have no `kind`. */
export type StepIn = Omit<WorkflowStepIn, "kind"> & { kind?: WorkflowStepIn["kind"] };

export const MAX_STEPS = 20;
export const STEP_SIZE = { width: 232, height: 80 };
const ROW = 140;
const COLUMN = 280;

export function normalizeStep(s: StepIn): Step {
  return {
    key: s.key,
    kind: s.kind ?? "agent",
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
  fields: { agent: string | null; title: string; changes_code: boolean; after?: string | null; kind?: Step["kind"] },
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
    kind: fields.kind ?? "agent",
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
  /** The published version it ran; null or missing for the draft. */
  version?: number | null;
  steps: StepIn[];
}

/** The workflow a run executed, frozen when it started (null for other runs). */
export function runWorkflow(run: Pick<Run, "config">): RunWorkflow | null {
  const workflow = (run.config as { workflow?: RunWorkflow | null }).workflow;
  return workflow ?? null;
}

/** "version 3" or "the draft": what a run, or a run about to start, executes. */
export function sourceLabel(version: number | null | undefined): string {
  return version ? `version ${version}` : "the draft";
}

/** Steps in the order they can run: everything a step waits for comes first, ties in layout order. */
export function executionOrder<T extends Pick<Step, "key" | "after" | "position">>(steps: T[]): T[] {
  const byKey = new Map(steps.map((s) => [s.key, s]));
  const layout = (a: T, b: T) => a.position.y - b.position.y || a.position.x - b.position.x;
  const placed = new Set<string>();
  const out: T[] = [];
  const remaining = [...steps].sort(layout);
  while (remaining.length) {
    const i = remaining.findIndex((s) => s.after.every((k) => placed.has(k) || !byKey.has(k)));
    // A loop (only in a draft with problems): keep layout order for the rest.
    const [next] = remaining.splice(i < 0 ? 0 : i, 1);
    placed.add(next.key);
    out.push(next);
  }
  return out;
}

export interface StepChange {
  key: string;
  title: string;
  /** What changed, in words ("instructions", "agent", "runs after"…). */
  fields: string[];
}

export interface SpecChanges {
  name: boolean;
  description: boolean;
  added: Step[];
  removed: Step[];
  changed: StepChange[];
  /** Steps that only moved on the canvas. */
  moved: number;
}

const FIELD_NAMES: Array<[keyof Step, string]> = [
  ["title", "name"],
  ["kind", "kind"],
  ["agent", "agent"],
  ["instructions", "instructions"],
  ["changes_code", "changes code"],
  ["after", "runs after"],
  ["model", "model"],
  ["max_turns", "max turns"],
  ["timeout_minutes", "time limit"],
  ["retries", "retries"],
];

/** What changed from `before` to `after`, step by step (steps are matched by key). */
export function specChanges(
  before: { name: string; description: string; steps: Step[] },
  after: { name: string; description: string; steps: Step[] },
): SpecChanges {
  const old = new Map(before.steps.map((s) => [s.key, s]));
  const now = new Map(after.steps.map((s) => [s.key, s]));
  const value = (s: Step, f: keyof Step) => JSON.stringify(f === "after" ? [...s.after].sort() : s[f]);
  const changed: StepChange[] = [];
  let moved = 0;
  for (const s of after.steps) {
    const was = old.get(s.key);
    if (!was) continue;
    const fields = FIELD_NAMES.filter(([f]) => value(s, f) !== value(was, f)).map(([, label]) => label);
    if (fields.length) changed.push({ key: s.key, title: s.title.trim() || "Untitled step", fields });
    else if (s.position.x !== was.position.x || s.position.y !== was.position.y) moved++;
  }
  return {
    name: before.name !== after.name,
    description: before.description !== after.description,
    added: after.steps.filter((s) => !old.has(s.key)),
    removed: before.steps.filter((s) => !now.has(s.key)),
    changed,
    moved,
  };
}

export function hasChanges(c: SpecChanges): boolean {
  return c.name || c.description || c.added.length > 0 || c.removed.length > 0 || c.changed.length > 0 || c.moved > 0;
}
