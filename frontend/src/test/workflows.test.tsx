import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Approval, ExecutionEvent, Task } from "@/api/types";
import { filterTasks, neighbours } from "@/components/board/filter";
import { TaskCardView } from "@/components/board/TaskCard";
import { ApprovalCard } from "@/components/run/ApprovalCard";
import { LaunchDialog } from "@/components/task/LaunchDialog";
import { TaskFormDialog } from "@/components/task/TaskFormDialog";
import { useEvents } from "@/stores/events";

import { baseTask, mockApi, renderWithProviders } from "./utils";

const approval: Approval = {
  id: "apr_1",
  run_id: "run_1",
  task_id: "tsk_1",
  session_id: "ses_1",
  subtask_id: null,
  kind: "tool_permission",
  status: "pending",
  title: "Run `npm`",
  description: "Install dependencies",
  tool_name: "Bash",
  tool_use_id: "toolu_1",
  tool_input: { command: "npm install left-pad" },
  data: { role: "implementer" },
  resolution: null,
  created_at: new Date().toISOString(),
  resolved_at: null,
  expires_at: null,
  task_key: "BOS-1",
  task_title: "Fix",
};

afterEach(() => vi.unstubAllGlobals());

describe("creating a task", () => {
  it("submits the form to the API", async () => {
    const { calls } = mockApi({
      "GET /workspaces": () => [{ id: "ws_1", name: "shop" }],
      "GET /tasks": () => [],
      "POST /tasks": (c) => ({ ...baseTask, ...(c.body as object) }),
    });
    const onOpenChange = vi.fn();
    renderWithProviders(<TaskFormDialog open onOpenChange={onOpenChange} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Title"), "Add retries to the client");
    await user.type(screen.getByLabelText("Description"), "Use exponential backoff");
    await user.click(screen.getByRole("radio", { name: "Agent team" }));
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({ title: "Add retries to the client", description: "Use exponential backoff", execution_mode: "team", workspace_id: "ws_1" });
  });

  it("does not submit without a title", async () => {
    mockApi({ "GET /workspaces": () => [], "GET /tasks": () => [] });
    renderWithProviders(<TaskFormDialog open onOpenChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Create task" })).toBeDisabled();
  });
});

describe("approvals", () => {
  it("allows a command and remembers it for the run", async () => {
    const { calls } = mockApi({ "POST /approvals/apr_1/resolve": () => ({ ...approval, status: "approved" }) });
    renderWithProviders(<ApprovalCard approval={approval} />);
    expect(screen.getByText("npm install left-pad")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /always allow/i }));
    await user.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ decision: "approve", remember: true });
  });

  it("denies with a reason", async () => {
    const { calls } = mockApi({ "POST /approvals/apr_1/resolve": () => ({ ...approval, status: "denied" }) });
    renderWithProviders(<ApprovalCard approval={approval} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /deny/i }));
    await user.type(screen.getByPlaceholderText(/tell the agent why/i), "No new dependencies{Enter}");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toMatchObject({ decision: "deny", message: "No new dependencies" });
  });
});

describe("launching a run", () => {
  const detail = { ...baseTask, activity: [], runs: [], dependencies: [], dependents: [] };
  const roles = [
    { id: "r1", key: "coordinator", name: "Coordinator", enabled: true, is_coordinator: true, color: "amber" },
    { id: "r2", key: "implementer", name: "Implementer", enabled: true, is_coordinator: false, color: "violet" },
  ];

  it("shows preview errors and blocks the launch", async () => {
    mockApi({
      "GET /roles": () => roles,
      "GET /settings": () => ({ execution: { default_mode: "single", backend: "claude", require_plan_approval: true }, provider: {} }),
      "POST /tasks/tsk_1/runs/preview": () => ({ can_start: false, errors: ["Not signed in to a Claude subscription — run `claude auth login`"], warnings: [], config: null, scope: {} }),
    });
    renderWithProviders(<LaunchDialog task={detail as never} open onOpenChange={() => {}} />);
    expect(await screen.findByText(/not signed in/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("starts exactly one run even when clicked twice", async () => {
    let resolveStart: (v: unknown) => void = () => {};
    const { calls } = mockApi({
      "GET /roles": () => roles,
      "GET /settings": () => ({ execution: { default_mode: "single", backend: "claude", require_plan_approval: true }, provider: {} }),
      "POST /tasks/tsk_1/runs/preview": () => ({
        can_start: true,
        errors: [],
        warnings: [],
        config: {},
        scope: { implementer: { name: "Implementer", profile: "edit_and_test", description: "Can edit files", tools: ["Read", "Edit", "Bash"], auto_allowed: ["Read", "Edit", "Bash(pytest:*)"] } },
        workspace: { path: "/code/shop", isolation: "worktree", base_branch: "main" },
        provider: { ready: true, summary: "Signed in with Claude (max)" },
      }),
      "POST /tasks/tsk_1/runs": () => new Promise((r) => (resolveStart = r)),
    });
    renderWithProviders(<LaunchDialog task={detail as never} open onOpenChange={() => {}} />);
    const start = await screen.findByRole("button", { name: /start run/i });
    await waitFor(() => expect(start).toBeEnabled());
    expect(screen.getByText(/isolated git worktree/i)).toBeInTheDocument();
    expect(screen.getByText(/asks you first/i)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(start);
    await user.click(start);
    const posts = calls.filter((c) => c.method === "POST" && c.url.endsWith("/runs"));
    expect(posts).toHaveLength(1);
    expect(posts[0].headers["Idempotency-Key"]).toBeTruthy();
    resolveStart({ id: "run_1" });
  });
});

describe("task cards", () => {
  it("shows agent state, approvals and dependencies", () => {
    const task = {
      ...baseTask,
      open_dependencies: 1,
      sources: [{ id: "s", kind: "github_issue", title: "Crash", url: "u", path: null, line: null, repo: "acme/shop", number: 42, state: "open" }],
      latest_run: { id: "run_1", number: 1, status: "waiting_approval", mode: "team", strategy: "team", backend: "claude", started_at: null, ended_at: null, error_kind: null, sessions: 2, active_sessions: 2, subtasks: 3, subtasks_done: 1, pending_approvals: 1, agents: [{ role: "implementer", name: "Implementer", status: "waiting_approval", count: 1 }], integration_status: "pending" },
    } as Task;
    renderWithProviders(<TaskCardView task={task} />);
    expect(screen.getByText("shop#42")).toBeInTheDocument();
    expect(screen.getByText("1/3")).toBeInTheDocument();
    expect(screen.getByText("Needs approval")).toBeInTheDocument();
    expect(screen.getByTitle("Waiting on dependencies")).toBeInTheDocument();
  });
});

describe("board logic", () => {
  const tasks = [
    { ...baseTask, id: "a", key: "BOS-1", title: "Fix login", labels: ["auth"], priority: "high" },
    { ...baseTask, id: "b", key: "BOS-2", title: "Write docs", labels: ["docs"], priority: "low", status: "done" },
    { ...baseTask, id: "c", key: "BOS-3", title: "Login rate limit", labels: ["auth"], priority: "low", latest_run: { status: "running", pending_approvals: 0 } },
  ] as Task[];
  const none = { q: "", labels: [], priorities: [], workspaceId: null, sources: [], agentState: "any" as const, showDone: true };

  it("filters by text, labels, priority, agent state and done", () => {
    expect(filterTasks(tasks, { ...none, q: "login" }).map((t) => t.id)).toEqual(["a", "c"]);
    expect(filterTasks(tasks, { ...none, labels: ["auth"], priorities: ["high"] }).map((t) => t.id)).toEqual(["a"]);
    expect(filterTasks(tasks, { ...none, agentState: "running" }).map((t) => t.id)).toEqual(["c"]);
    expect(filterTasks(tasks, { ...none, showDone: false }).map((t) => t.id)).toEqual(["a", "c"]);
  });

  it("computes neighbours for fractional ordering", () => {
    expect(neighbours(["a", "b", "c"], "b")).toEqual({ after_id: "a", before_id: "c" });
    expect(neighbours(["a", "b"], "a")).toEqual({ after_id: null, before_id: "b" });
  });
});

describe("event store", () => {
  const ev = (seq: number): ExecutionEvent => ({ seq, id: `e${seq}`, run_id: "run_x", task_id: null, session_id: null, agent_id: null, parent_agent_id: null, type: "tool.call", status: null, ts: "2026-10-19T10:00:00Z", data: {} });

  it("holds live events that arrive while history loads and merges them in order", async () => {
    let release: () => void = () => {};
    mockApi({
      "GET /runs/run_x/events": () =>
        new Promise((r) => {
          release = () => r({ events: [ev(1), ev(2), ev(3)], last_seq: 3, has_more: false });
        }),
    });
    const loading = useEvents.getState().load("run_x");
    useEvents.getState().ingest([ev(4), ev(3)]);
    expect(useEvents.getState().runs.run_x.events).toEqual([]);
    release();
    await loading;
    expect(useEvents.getState().runs.run_x.events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    useEvents.getState().ingest([ev(5), ev(4)]);
    expect(useEvents.getState().runs.run_x.events.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(useEvents.getState().runs.run_x.lastSeq).toBe(5);
  });
});
