import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Run } from "@/api/types";
import { ApprovalCard } from "@/components/run/ApprovalCard";
import { RunSteps } from "@/components/run/RunSteps";
import { RunWorkflowDialog } from "@/components/workflow/RunWorkflowDialog";
import { NewWorkflowDialog } from "@/components/workflow/templates";
import { PublishDialog } from "@/components/workflow/versions";
import { normalizeStep } from "@/lib/workflow";
import pausedRun from "@/test/fixtures/workflow-paused-approval.run.json";

import { mockApi, renderWithProviders } from "./utils";

const roles = [
  { id: "r2", key: "implementer", name: "Implementer", enabled: true, is_coordinator: false, permission_profile: "edit_and_test", color: "violet" },
  { id: "r3", key: "reviewer", name: "Reviewer", enabled: true, is_coordinator: false, permission_profile: "read_only", color: "rose" },
];
const spec = {
  steps: [
    { key: "build", kind: "agent" as const, title: "Build it", agent: "implementer", instructions: "Make the change.", changes_code: true, after: [], position: { x: 0, y: 0 } },
    { key: "check", kind: "approval" as const, title: "Check it", agent: null, instructions: "Does it work?", changes_code: false, after: ["build"], position: { x: 0, y: 140 } },
  ],
};
const version = (number: number, over: Record<string, unknown> = {}) => ({
  number,
  name: "Fix and review",
  note: `Note ${number}`,
  steps: 2,
  created_at: "2026-10-21T09:00:00Z",
  draft_revision: number * 2,
  run_count: 0,
  workflow_id: "wf_1",
  description: "",
  spec,
  agents: [{ key: "implementer", name: "Implementer", permission_profile: "edit_and_test", model: null, color: "violet" }],
  agent_changes: [],
  issues: [],
  is_latest: number === 2,
  ...over,
});
const draft = { name: "Fix and review", description: "", steps: spec.steps.map(normalizeStep) };

describe("publishing", () => {
  it("shows what changed since the latest version and publishes with a note", async () => {
    const { calls } = mockApi({ "POST /workflows/wf_1/versions": () => [201, version(3)] });
    const changed = { ...draft, steps: [{ ...draft.steps[0], instructions: "Make a smaller change." }, draft.steps[1]] };
    renderWithProviders(
      <PublishDialog open onOpenChange={() => {}} workflowId="wf_1" revision={7} draft={changed} latest={version(2) as never} roles={roles as never} errors={[]} saving={false} />,
    );
    const dialog = screen.getByRole("dialog", { name: "Publish version 3" });
    expect(within(dialog).getByText("Changes since version 2")).toBeInTheDocument();
    expect(within(dialog).getByText("Build it: instructions")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(within(dialog).getByRole("textbox", { name: /Note/ }), "Smaller change");
    await user.click(within(dialog).getByRole("button", { name: /Publish version 3/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ revision: 7, note: "Smaller change" });
  });

  it("has nothing to publish when the draft is the latest version", () => {
    mockApi({});
    renderWithProviders(
      <PublishDialog open onOpenChange={() => {}} workflowId="wf_1" revision={7} draft={draft} latest={version(2) as never} roles={roles as never} errors={[]} saving={false} />,
    );
    expect(screen.getByRole("button", { name: /Publish version 3/ })).toBeDisabled();
    expect(screen.getByText("Nothing has changed since version 2")).toBeInTheDocument();
  });
});

describe("running a version", () => {
  const dialogApi = () =>
    mockApi({
      "GET /tasks": () => [],
      "GET /workspaces": () => [{ id: "ws_1", name: "shop", path: "/home/me/code/shop" }],
      "GET /settings": () => ({ execution: { max_parallel_per_run: 2, backend: "demo" } }),
      "GET /workflows/wf_1/versions": () => [version(2), version(1)],
      "GET /workflows/wf_1/versions/2": () => version(2),
      "GET /workflows/wf_1/versions/1": () => version(1, { agent_changes: [{ key: "implementer", name: "Implementer", state: "changed", fields: ["model"] }] }),
      "POST /workflows/wf_1/runs": () => [201, { id: "run_9", number: 9, task_key: "BOS-9" }],
    });

  it("runs the latest version unless another one, or the draft, is chosen", async () => {
    const { calls } = dialogApi();
    renderWithProviders(
      <RunWorkflowDialog open onOpenChange={() => {}} workflowId="wf_1" name="Fix and review" revision={7} steps={draft.steps} roles={roles as never} draftState="unpublished changes" />,
    );
    const what = await screen.findByRole("combobox", { name: /^What to run/ });
    await waitFor(() => expect(what).toHaveValue("2"));
    expect(await screen.findByText(/The run waits for you at Check it/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "Task title" }), "Dark mode");
    await user.click(screen.getByRole("button", { name: /Start run/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ revision: 7, version: 2, new_task: { title: "Dark mode" } });

    await user.selectOptions(what, "1");
    expect(await screen.findByText(/as it was when it was published/)).toBeInTheDocument();
    await user.selectOptions(what, "draft");
    expect(screen.getByText("The draft as it is now")).toBeInTheDocument();
  });
});

describe("approval steps", () => {
  it("in the Steps view: says the run is paused, and approves with a note", async () => {
    const run = pausedRun as unknown as Run;
    const pending = run.approvals.find((a) => a.status === "pending")!;
    const { calls } = mockApi({ [`POST /approvals/${pending.id}/resolve`]: () => ({ ...pending, status: "approved" }) });
    renderWithProviders(<RunSteps run={run} selected={null} onSelect={() => {}} onOpenTranscript={() => {}} />);
    const panel = await screen.findByRole("complementary", { name: "Approve the plan details" });
    expect(within(panel).getByText(/The run is paused: decide now/)).toBeInTheDocument();
    expect(within(panel).getByText("Is the cause right?")).toBeInTheDocument();
    expect(panel).toHaveTextContent(/Approve to continue with Implement/);
    const user = userEvent.setup();
    await user.type(within(panel).getByRole("textbox", { name: "Note for the decision" }), "Keep it small");
    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ decision: "approve", message: "Keep it small", remember: false });
  });

  it("in the Steps view: lists the steps in order with their reasons", async () => {
    mockApi({});
    const user = userEvent.setup();
    renderWithProviders(<RunSteps run={pausedRun as unknown as Run} selected={null} onSelect={() => {}} onOpenTranscript={() => {}} />);
    await user.click(screen.getByRole("radio", { name: "List" }));
    const list = screen.getByRole("list", { name: "Steps in the order they run" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.querySelector(".text-base")?.textContent)).toEqual(["Research", "Approve the plan", "Implement", "Final check", "Check the docs"]);
    expect(within(items[2]).getByText("Waits for Approve the plan. The run is paused.")).toBeInTheDocument();
  });

  it("a decided approval can't be decided again", () => {
    mockApi({});
    const run = pausedRun as unknown as Run;
    const decided = { ...run.approvals[0], status: "denied", resolution: { decision: "deny", message: "Wrong cause" }, resolved_at: "2026-10-21T10:00:00Z" };
    renderWithProviders(<ApprovalCard approval={decided} />);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByText(/Rejected .*: Wrong cause/)).toBeInTheDocument();
  });
});

describe("templates", () => {
  const template = {
    key: "fix-with-review",
    name: "Fix with review",
    description: "Find the cause, fix it, then test and review the change.",
    outline: "Research → Implement",
    spec,
    setup: [
      { level: "required", done: true, message: "The Implementer agent is turned on", link: null },
      { level: "required", done: false, message: "Turn on the Tester agent in Settings, or choose another agent for its steps.", link: "/settings" },
      { level: "recommended", done: false, message: "Add your test commands in Settings.", link: "/settings" },
    ],
  };

  it("previews the steps and what to set up, then creates an independent copy", async () => {
    const { calls } = mockApi({
      "GET /roles": () => roles,
      "GET /workflow-templates": () => [template],
      "POST /workflows": () => [201, { id: "wf_new" }],
    });
    renderWithProviders(<NewWorkflowDialog open onOpenChange={() => {}} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Fix with review/ }));
    const dialog = screen.getByRole("dialog", { name: "New workflow from Fix with review" });
    expect(within(dialog).getByText("Turn on the Tester agent in Settings, or choose another agent for its steps.")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("link", { name: "Open Settings" })).toHaveLength(2);
    expect(within(dialog).getByText("Approval by you, after Build it")).toBeInTheDocument();
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Our fix flow");
    await user.click(screen.getByRole("button", { name: /Create workflow/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Our fix flow", template: "fix-with-review" });
  });
});
