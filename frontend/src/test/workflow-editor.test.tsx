import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import { RunWorkflowDialog } from "@/components/workflow/RunWorkflowDialog";
import { normalizeStep } from "@/lib/workflow";
import { WorkflowEditorPage } from "@/pages/WorkflowEditorPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";

import { baseTask, mockApi, renderWithProviders } from "./utils";

const roles = [
  { id: "r1", key: "coordinator", name: "Coordinator", enabled: true, is_coordinator: true, permission_profile: "read_only", color: "amber" },
  { id: "r2", key: "implementer", name: "Implementer", enabled: true, is_coordinator: false, permission_profile: "edit_and_test", color: "violet" },
  { id: "r3", key: "reviewer", name: "Reviewer", enabled: true, is_coordinator: false, permission_profile: "read_only", color: "teal" },
];

const steps = [
  { key: "step-1", title: "Build it", agent: "implementer", instructions: "Make the change.", changes_code: true, after: [], position: { x: 0, y: 0 } },
  { key: "step-2", title: "Review it", agent: "ghost", instructions: "Check it.", changes_code: false, after: ["step-1"], position: { x: 0, y: 140 } },
];

const workflow = (over: Record<string, unknown> = {}) => ({
  id: "wf_1",
  name: "Fix and review",
  description: "",
  spec: { steps },
  revision: 3,
  created_at: "2026-10-20T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
  issues: [],
  ...over,
});

const unknownAgent = { level: "error", code: "unknown_agent", message: "There is no agent called ghost. Choose another agent.", steps: ["step-2"], field: "agent" };

function editorApi(routes: Record<string, (call: never) => unknown> = {}) {
  return mockApi({
    "GET /workflows/wf_1": () => workflow({ issues: [unknownAgent] }),
    "GET /roles": () => roles,
    "GET /runs": () => [],
    ...routes,
  });
}

const renderEditor = () =>
  renderWithProviders(
    <Routes>
      <Route path="/workflows/:workflowId" element={<WorkflowEditorPage />} />
    </Routes>,
    { route: "/workflows/wf_1" },
  );

describe("workflow list", () => {
  it("offers to create the first workflow, blank or from a template", async () => {
    const { calls } = mockApi({
      "GET /workflows": () => [],
      "GET /workflow-templates": () => [],
      "POST /workflows": () => [201, workflow({ id: "wf_new", name: "Untitled workflow", spec: { steps: [] } })],
    });
    renderWithProviders(<WorkflowsPage />, { route: "/workflows" });
    expect(await screen.findByText("No workflows yet")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Start from a template" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /blank workflow/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Untitled workflow" });
  });

  it("lists workflows with their size and latest run", async () => {
    mockApi({
      "GET /workflows": () => [
        { id: "wf_1", name: "Fix and review", description: "Build, then review", steps: 2, revision: 3, updated_at: "2026-10-20T09:00:00Z", run_count: 1, last_run: { id: "run_1", number: 4, status: "succeeded", task_id: "tsk_1", task_key: "BOS-1", created_at: "2026-10-20T09:00:00Z" } },
        { id: "wf_2", name: "Plan only", description: "", steps: 1, revision: 1, updated_at: "2026-10-20T09:00:00Z", run_count: 0, last_run: null },
      ],
    });
    renderWithProviders(<WorkflowsPage />, { route: "/workflows" });
    expect(await screen.findByText("Fix and review")).toBeInTheDocument();
    expect(screen.getByText("2 steps")).toBeInTheDocument();
    expect(screen.getByText(/BOS-1/)).toBeInTheDocument();
    expect(screen.getByText("Never run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new workflow/i })).toBeInTheDocument();
  });
});

describe("workflow editor", () => {
  it("lists problems and keeps Run disabled until they are fixed", async () => {
    editorApi();
    renderEditor();
    const problems = await screen.findByRole("button", { name: /1 problem/i });
    expect(screen.getByRole("button", { name: /^run$/i })).toBeDisabled();
    await userEvent.setup().click(problems);
    const panel = screen.getByRole("region", { name: "Problems" });
    expect(panel).toHaveTextContent("Review it: There is no agent called ghost. Choose another agent.");
  });

  it("selects a step added from the toolbar without fighting the canvas over the selection", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    editorApi({ "PATCH /workflows/wf_1": () => workflow({ revision: 4 }) });
    renderEditor();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /add a step/i }));
    await user.click(await screen.findByRole("menuitem", { name: /^Reviewer/ }));
    expect(await screen.findByRole("complementary", { name: "Reviewer settings" })).toBeInTheDocument();
    // Focus goes to the new step's name, ready to type over, not back to the menu button.
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Reviewer");
    expect(errors.mock.calls.filter((c) => String(c[0]).includes("Maximum update depth"))).toEqual([]);
    errors.mockRestore();
  });

  it("selects a step added from a step's own add button", async () => {
    editorApi({ "PATCH /workflows/wf_1": () => workflow({ revision: 4 }) });
    renderEditor();
    const user = userEvent.setup();
    // React Flow hides nodes until it has measured them, which jsdom never does, so they have no
    // accessible name to query by role; their labels still work.
    // A plain click: pressing a step also starts React Flow's drag handling, which jsdom can't run.
    fireEvent.click(await screen.findByLabelText(/^Step Build it/));
    expect(await screen.findByRole("complementary", { name: "Build it settings" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("Add a step after Build it"));
    await user.click(await screen.findByRole("menuitem", { name: /^Reviewer/ }));
    expect(await screen.findByRole("complementary", { name: "Reviewer settings" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus());
  });

  it("undoes an added step in one step, even though its name was focused", async () => {
    editorApi({ "PATCH /workflows/wf_1": () => workflow({ revision: 4 }) });
    renderEditor();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /add a step/i }));
    await user.click(await screen.findByRole("menuitem", { name: /^Reviewer/ }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveFocus());
    expect(screen.getByLabelText(/^Step Reviewer/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: /^Undo/ }));
    expect(screen.queryByLabelText(/^Step Reviewer/)).not.toBeInTheDocument();
  });

  it("makes a step wait for another from its settings, offering only steps that can't loop", async () => {
    const three = [
      { key: "a", title: "Plan", agent: "implementer", instructions: "x", changes_code: false, after: [], position: { x: 0, y: 0 } },
      { key: "b", title: "Build", agent: "implementer", instructions: "x", changes_code: true, after: ["a"], position: { x: 0, y: 140 } },
      { key: "c", title: "Check", agent: "reviewer", instructions: "x", changes_code: false, after: [], position: { x: 280, y: 0 } },
    ];
    const { calls } = editorApi({
      "GET /workflows/wf_1": () => workflow({ spec: { steps: three } }),
      "PATCH /workflows/wf_1": () => workflow({ spec: { steps: three }, revision: 4 }),
    });
    renderEditor();
    const user = userEvent.setup();
    // A plain click: pressing a step also starts React Flow's drag handling, which jsdom can't run.
    fireEvent.click(await screen.findByLabelText(/^Step Plan/));
    await user.click(await screen.findByRole("button", { name: "Wait for another step" }));
    const options = (await screen.findAllByRole("menuitem")).map((i) => i.textContent);
    expect(options).toEqual(["Check"]);
    await user.click(screen.getByRole("menuitem", { name: "Check" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true), { timeout: 2000 });
    const saved = (calls.filter((c) => c.method === "PATCH").at(-1)!.body as { spec: { steps: { key: string; after: string[] }[] } }).spec.steps;
    expect(saved.find((st) => st.key === "a")!.after).toEqual(["c"]);
  });

  it("says when the workflow does not exist", async () => {
    mockApi({ "GET /workflows/wf_1": () => [404, { detail: "Workflow not found" }], "GET /roles": () => roles, "GET /runs": () => [] });
    renderEditor();
    expect(await screen.findByText("This workflow doesn't exist")).toBeInTheDocument();
  });

  it("saves edits against the revision they started from", async () => {
    const { calls } = editorApi({
      "PATCH /workflows/wf_1": (c: { body: { name: string } }) => workflow({ name: c.body.name, revision: 4 }),
    });
    renderEditor();
    const name = await screen.findByRole("textbox", { name: "Name" });
    const user = userEvent.setup();
    await user.clear(name);
    await user.type(name, "Ship it");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved"), { timeout: 3000 });
    const saves = calls.filter((c) => c.method === "PATCH");
    expect(saves.length).toBeGreaterThan(0);
    expect(saves[0].body).toMatchObject({ revision: 3 });
    expect(saves.at(-1)!.body).toMatchObject({ name: "Ship it" });
    expect(saves.slice(1).every((s) => (s.body as { revision: number }).revision === 4)).toBe(true);
  });

  it("turns a save that lost to another tab into a choice, not an overwrite", async () => {
    let revision = 3;
    const { calls } = editorApi({
      "GET /workflows/wf_1": () => workflow({ revision, issues: [unknownAgent] }),
      "PATCH /workflows/wf_1": (c: { body: { revision: number } }) =>
        c.body.revision === 5 ? workflow({ revision: 6 }) : [409, { detail: "This workflow was changed somewhere else since you opened it. Reload to see the latest version." }],
    });
    renderEditor();
    const name = await screen.findByRole("textbox", { name: "Name" });
    revision = 5; // another tab saved in the meantime
    const user = userEvent.setup();
    await user.type(name, "!");
    expect(await screen.findByText("This workflow was changed in another tab or window", undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^run$/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Keep mine" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)!.body).toMatchObject({ revision: 5, name: "Fix and review!" }));
    await waitFor(() => expect(screen.queryByText("This workflow was changed in another tab or window")).not.toBeInTheDocument());
  });
});

describe("running a workflow", () => {
  const dialogApi = (start: () => unknown) =>
    mockApi({
      "GET /tasks": () => [baseTask, { ...baseTask, id: "tsk_2", key: "BOS-2", title: "Already busy", latest_run: { id: "run_9", status: "running" } }],
      "GET /workspaces": () => [{ id: "ws_1", name: "shop", path: "/home/me/code/shop" }],
      "GET /settings": () => ({ execution: { max_parallel_per_run: 2 } }),
      "GET /providers/status": () => ({ ready: true, summary: "Signed in with Claude (max)" }),
      "GET /workflows/wf_1/versions": () => [],
      "POST /workflows/wf_1/runs": start,
    });

  const renderDialog = (saving = false) =>
    renderWithProviders(
      <RunWorkflowDialog open onOpenChange={() => {}} workflowId="wf_1" name="Fix and review" revision={3} saving={saving} steps={steps.map(normalizeStep)} roles={roles as never} draftState="not published" />,
    );

  it("starts one run on a new task, however often Start is clicked", async () => {
    let resolveStart: (v: unknown) => void = () => {};
    const { calls } = dialogApi(() => new Promise((r) => (resolveStart = r)));
    renderDialog();
    const user = userEvent.setup();
    await user.type(await screen.findByRole("textbox", { name: "Task title" }), "Add a dark mode");
    expect(await screen.findByText("2 steps, up to 2 agents at a time")).toBeInTheDocument();
    expect(screen.getByText("Signed in with Claude (max)")).toBeInTheDocument();
    const start = screen.getByRole("button", { name: /start run/i });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await user.click(start);
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ revision: 3, new_task: { title: "Add a dark mode", description: "", workspace_id: "ws_1" } });
    expect(posts[0].headers["Idempotency-Key"]).toBeTruthy();
    resolveStart({ id: "run_1", number: 1, task_key: "BOS-3" });
  });

  it("only offers tasks that can take a run, and shows why the server refused", async () => {
    dialogApi(() => [422, { detail: "Fix this workflow before running it. Review it: There is no agent called ghost." }]);
    renderDialog();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Existing task" }));
    const busy = await screen.findByRole("radio", { name: /already busy/i });
    expect(busy).toBeDisabled();
    expect(busy).toHaveTextContent("Already running");
    await user.click(screen.getByRole("radio", { name: /fix the crash/i }));
    await user.click(screen.getByRole("button", { name: /start run/i }));
    expect(await screen.findByText(/there is no agent called ghost/i)).toBeInTheDocument();
  });

  it("waits for unsaved changes before starting", async () => {
    dialogApi(() => ({}));
    renderDialog(true);
    await userEvent.setup().type(await screen.findByRole("textbox", { name: "Task title" }), "Add a dark mode");
    expect(screen.getByText("Saving your latest changes…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });
});
