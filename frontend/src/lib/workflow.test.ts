import { addStep, connect, disconnect, executionOrder, hasChanges, newStepKey, normalizeStep, removeSteps, specChanges, type Step, tidy, upstream } from "./workflow";

const step = (key: string, after: string[] = [], position = { x: 0, y: 0 }): Step => normalizeStep({ key, title: key, agent: "implementer", instructions: "", changes_code: true, after, position });

describe("workflow drafts", () => {
  it("adds a step after another, below it and beside its siblings", () => {
    const steps = [step("plan", [], { x: 100, y: 0 }), step("build", ["plan"], { x: 100, y: 140 })];
    const added = addStep(steps, { agent: "reviewer", title: "Reviewer", changes_code: false, after: "plan" });
    const s = added.steps.find((x) => x.key === added.key)!;
    expect(added.key).toBe("step-3");
    expect(s.after).toEqual(["plan"]);
    expect(s.position).toEqual({ x: 380, y: 140 });
  });

  it("adds a first step at the origin and later loose steps below the lowest", () => {
    const first = addStep([], { agent: "planner", title: "Planner", changes_code: false });
    expect(first.steps[0].position).toEqual({ x: 0, y: 0 });
    expect(first.steps[0].after).toEqual([]);
    const second = addStep(first.steps, { agent: "planner", title: "Planner", changes_code: false });
    expect(second.steps[1].position).toEqual({ x: 0, y: 140 });
  });

  it("never reuses a step key", () => {
    expect(newStepKey([step("step-2"), step("step-3")])).toBe("step-4");
    expect(newStepKey([step("step-1")])).toBe("step-2");
  });

  it("refuses connections that would make steps wait for each other", () => {
    const steps = [step("a"), step("b", ["a"]), step("c", ["b"])];
    expect(connect(steps, "c", "a")).toBe("c already waits for a, so they would wait for each other.");
    expect(connect(steps, "a", "a")).toBe("A step can't run after itself.");
    const joined = connect(steps, "a", "c");
    expect(Array.isArray(joined) && joined.find((s) => s.key === "c")!.after).toEqual(["b", "a"]);
    expect([...upstream(steps, "c")].sort()).toEqual(["a", "b"]);
  });

  it("drops connections to removed steps", () => {
    const steps = [step("a"), step("b", ["a"]), step("c", ["a", "b"])];
    expect(removeSteps(steps, ["a"]).map((s) => [s.key, s.after])).toEqual([
      ["b", []],
      ["c", ["b"]],
    ]);
    expect(disconnect(steps, "a", "c").find((s) => s.key === "c")!.after).toEqual(["b"]);
  });

  it("tidies steps top to bottom with parallel steps side by side", () => {
    const laid = tidy([step("a"), step("b", ["a"]), step("c", ["a"]), step("d", ["b", "c"])]);
    const at = Object.fromEntries(laid.map((s) => [s.key, s.position]));
    expect(at.b.y).toBeGreaterThan(at.a.y);
    expect(at.b.y).toBe(at.c.y);
    expect(at.b.x).not.toBe(at.c.x);
    expect(at.d.y).toBeGreaterThan(at.b.y);
  });
});

describe("versions: what changed", () => {
  const base = { name: "Fix", description: "", steps: [step("a"), step("b", ["a"], { x: 0, y: 140 })] };

  it("names added, removed and changed steps, and the fields that changed", () => {
    const after = {
      name: "Fix it",
      description: "",
      steps: [{ ...base.steps[0], instructions: "Look closer.", after: [] }, step("c", ["a"])],
    };
    const changes = specChanges(base, after);
    expect(changes.name).toBe(true);
    expect(changes.added.map((s) => s.key)).toEqual(["c"]);
    expect(changes.removed.map((s) => s.key)).toEqual(["b"]);
    expect(changes.changed).toEqual([{ key: "a", title: "a", fields: ["instructions"] }]);
    expect(hasChanges(changes)).toBe(true);
  });

  it("counts layout-only moves, ignores the order of waits, and finds no change in a copy", () => {
    const moved = { ...base, steps: [{ ...base.steps[0], position: { x: 50, y: 0 } }, base.steps[1]] };
    expect(specChanges(base, moved)).toMatchObject({ moved: 1, changed: [] });
    const joined = { ...base, steps: [step("x"), step("y"), step("z", ["x", "y"])] };
    const reordered = { ...joined, steps: [step("x"), step("y"), step("z", ["y", "x"])] };
    expect(hasChanges(specChanges(joined, reordered))).toBe(false);
    expect(hasChanges(specChanges(base, structuredClone(base)))).toBe(false);
  });

  it("reads steps saved without a kind as agent steps", () => {
    expect(normalizeStep({ key: "old", title: "Old", instructions: "", changes_code: false }).kind).toBe("agent");
  });

  it("orders steps so that everything a step waits for comes first", () => {
    const steps = [step("final", ["left", "right"], { x: 0, y: 280 }), step("right", ["start"], { x: 280, y: 140 }), step("left", ["start"], { x: 0, y: 140 }), step("start")];
    expect(executionOrder(steps).map((s) => s.key)).toEqual(["start", "left", "right", "final"]);
  });
});
