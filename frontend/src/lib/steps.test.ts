import type { ExecutionEvent, Run } from "@/api/types";
import approvedEvents from "@/test/fixtures/workflow-approved.json";
import approvedRun from "@/test/fixtures/workflow-approved.run.json";
import pausedEvents from "@/test/fixtures/workflow-paused-approval.json";
import pausedRun from "@/test/fixtures/workflow-paused-approval.run.json";
import rejectedEvents from "@/test/fixtures/workflow-rejected.json";
import rejectedRun from "@/test/fixtures/workflow-rejected.run.json";
import workflowRun from "@/test/fixtures/workflow-run.json";

import { reduceEvents } from "./graph/reduce";
import { defaultStep, stepRows } from "./steps";

// Recorded from the demo backend by scripts/export_fixtures.py: the events the live graph is built
// from, and the run as the API returned it at the same moment (what the Steps view reads).
const recorded: Array<[string, ExecutionEvent[], Run]> = [
  ["approved", approvedEvents as ExecutionEvent[], approvedRun as unknown as Run],
  ["rejected", rejectedEvents as ExecutionEvent[], rejectedRun as unknown as Run],
  ["paused at an approval", pausedEvents as ExecutionEvent[], pausedRun as unknown as Run],
];

const byKey = (run: Run) => new Map(stepRows(run).map((r) => [r.step.key, r]));

describe("the Steps view and the live graph agree", () => {
  it.each(recorded)("%s: every step's status, and the run's", (_, events, run) => {
    const model = reduceEvents(events);
    const graph = new Map(
      [...model.nodes.values()].filter((n) => n.kind === "subtask").map((n) => [n.data.key as string, (n.data.status as string | undefined) ?? "pending"]),
    );
    const steps = new Map(stepRows(run).map((r) => [r.step.key, r.subtask?.status]));
    expect(Object.fromEntries(graph)).toEqual(Object.fromEntries(steps));
    expect(model.meta.runStatus).toBe(run.status);
  });

  it.each(recorded)("%s: approval steps are drawn as their step, not as a separate request", (_, events) => {
    const model = reduceEvents(events);
    const approval = [...model.nodes.values()].find((n) => n.kind === "subtask" && n.data.stepKind === "approval");
    expect(approval?.sublabel).toBe("Approval by you");
    expect([...model.nodes.values()].filter((n) => n.kind === "approval")).toHaveLength(0);
  });

  it("reads runs recorded before approval steps existed as agent steps", () => {
    const model = reduceEvents(workflowRun as ExecutionEvent[]);
    const steps = [...model.nodes.values()].filter((n) => n.kind === "subtask");
    expect(steps.length).toBe(5);
    expect(steps.every((n) => n.data.stepKind === "agent")).toBe(true);
  });
});

describe("why each step is where it is", () => {
  it("an approved step carries the approver's note", () => {
    const rows = byKey(approvedRun as unknown as Run);
    expect(rows.get("approve")).toMatchObject({ state: "success", label: "Approved", reason: "Note from the approver: Keep it small." });
    expect(rows.get("implement")).toMatchObject({ state: "success", label: "Done" });
    expect(defaultStep([...rows.values()])).toBeNull();
  });

  it("a rejection skips what follows it and says why; the other branch finished", () => {
    const rows = byKey(rejectedRun as unknown as Run);
    expect(rows.get("approve")).toMatchObject({ state: "rejected", label: "Rejected", reason: "You rejected it." });
    for (const key of ["implement", "final"]) {
      expect(rows.get(key)).toMatchObject({ state: "skipped", label: "Skipped", reason: "Not run: it waits for Approve the plan, which was rejected." });
    }
    expect(rows.get("docs")).toMatchObject({ state: "success" });
    expect(defaultStep([...rows.values()])).toBe("approve");
  });

  it("a paused run keeps its approval waiting and says the run is paused", () => {
    const run = pausedRun as unknown as Run;
    const rows = byKey(run);
    expect(rows.get("approve")?.state).toBe("waiting_approval");
    expect(rows.get("approve")?.reason).toMatch(/The run is paused: decide now/);
    expect(rows.get("approve")?.approval?.status).toBe("pending");
    expect(rows.get("implement")).toMatchObject({ label: "Waiting", reason: "Waits for Approve the plan. The run is paused." });
    expect(rows.get("docs")).toMatchObject({ state: "paused", label: "Paused", reason: "Continues when you resume the run." });
    expect(defaultStep([...rows.values()])).toBe("approve");
  });

  it("lists steps in the order they can run", () => {
    const order = stepRows(approvedRun as unknown as Run).map((r) => r.step);
    expect(order.map((s) => s.key)).toEqual(["research", "approve", "implement", "final", "docs"]);
    order.forEach((s, i) => expect(s.after.every((k) => order.slice(0, i).some((d) => d.key === k))).toBe(true));
  });
});
