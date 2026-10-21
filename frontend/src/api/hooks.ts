import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError, qs } from "./client";
import type {
  AppSettings,
  Approval,
  Browse,
  Connection,
  ConnectionCreate,
  Dashboard,
  Diff,
  GitHubRepo,
  GitHubStatus,
  Meta,
  OverviewGraph,
  ProviderStatus,
  Role,
  RoleIn,
  Run,
  RunCreate,
  RunListItem,
  RunPreview,
  SourceRecordPage,
  Task,
  TaskCreate,
  TaskDetail,
  TaskStatus,
  TaskUpdate,
  Workflow,
  WorkflowListItem,
  WorkflowRunCreate,
  WorkflowUpdate,
  Workspace,
} from "./types";

export const keys = {
  meta: ["meta"] as const,
  dashboard: ["dashboard"] as const,
  tasks: ["tasks"] as const,
  task: (id: string) => ["task", id] as const,
  labels: ["labels"] as const,
  workspaces: ["workspaces"] as const,
  records: (params: object) => ["records", params] as const,
  runs: (params: object = {}) => ["runs", params] as const,
  run: (id: string) => ["run", id] as const,
  diff: (id: string) => ["diff", id] as const,
  approvals: ["approvals"] as const,
  settings: ["settings"] as const,
  roles: ["roles"] as const,
  provider: ["provider"] as const,
  providers: ["providers"] as const,
  connections: ["connections"] as const,
  github: ["github-status"] as const,
  repos: (q: string) => ["github-repos", q] as const,
  overview: ["overview-graph"] as const,
  workflows: ["workflows"] as const,
  workflow: (id: string) => ["workflow", id] as const,
};

export function notifyError(error: unknown, fallback = "Something went wrong") {
  const message = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : fallback;
  toast.error(message);
}

// -- queries ---------------------------------------------------------------------------
export const useMeta = () => useQuery({ queryKey: keys.meta, queryFn: () => api.get<Meta>("/meta"), staleTime: 60_000 });
export const useDashboard = () => useQuery({ queryKey: keys.dashboard, queryFn: () => api.get<Dashboard>("/dashboard") });
export const useTasks = () => useQuery({ queryKey: keys.tasks, queryFn: () => api.get<Task[]>("/tasks") });
export const useTask = (id: string | null) =>
  useQuery({ queryKey: keys.task(id ?? ""), queryFn: () => api.get<TaskDetail>(`/tasks/${id}`), enabled: !!id });
export const useLabels = () => useQuery({ queryKey: keys.labels, queryFn: () => api.get<string[]>("/tasks/labels") });
export const useWorkspaces = () => useQuery({ queryKey: keys.workspaces, queryFn: () => api.get<Workspace[]>("/workspaces") });
export const useRecords = (params: { triage: string; workspace_id?: string; connection_id?: string; kind?: string; q?: string; state?: string }) =>
  useQuery({
    queryKey: keys.records(params),
    queryFn: () => api.get<SourceRecordPage>(`/sources/records${qs({ ...params, limit: 500 })}`),
    placeholderData: (prev) => prev,
  });
export const useRuns = (params: { status?: string; task_id?: string; workflow_id?: string } = {}) =>
  useQuery({ queryKey: keys.runs(params), queryFn: () => api.get<RunListItem[]>(`/runs${qs(params)}`) });
export const useRun = (id: string | null) =>
  useQuery({ queryKey: keys.run(id ?? ""), queryFn: () => api.get<Run>(`/runs/${id}`), enabled: !!id });
export const useDiff = (id: string | null, enabled = true) =>
  useQuery({ queryKey: keys.diff(id ?? ""), queryFn: () => api.get<Diff>(`/runs/${id}/diff`), enabled: !!id && enabled });
export const useApprovals = () =>
  useQuery({ queryKey: keys.approvals, queryFn: () => api.get<Approval[]>("/approvals?status=pending") });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: () => api.get<AppSettings>("/settings") });
export const useRoles = () => useQuery({ queryKey: keys.roles, queryFn: () => api.get<Role[]>("/roles") });
export const useProviderStatus = () =>
  useQuery({ queryKey: keys.provider, queryFn: () => api.get<ProviderStatus>("/providers/status"), staleTime: 30_000 });
export const useProviders = () =>
  useQuery({ queryKey: keys.providers, queryFn: () => api.get<ProviderStatus[]>("/providers"), staleTime: 30_000 });
export const useConnections = () => useQuery({ queryKey: keys.connections, queryFn: () => api.get<Connection[]>("/connections") });
export const useGitHubStatus = () =>
  useQuery({ queryKey: keys.github, queryFn: () => api.get<GitHubStatus>("/github/status"), staleTime: 60_000 });
export const useGitHubRepos = (q: string, enabled: boolean) =>
  useQuery({
    queryKey: keys.repos(q),
    queryFn: () => api.get<GitHubRepo[]>(`/github/repos${qs({ q })}`),
    enabled,
    staleTime: 60_000,
  });
export const useOverviewGraph = () => useQuery({ queryKey: keys.overview, queryFn: () => api.get<OverviewGraph>("/graph/overview") });
export const useBrowse = (path: string | null) =>
  useQuery({ queryKey: ["browse", path], queryFn: () => api.get<Browse>(`/fs/browse${qs({ path })}`) });

// -- mutations -------------------------------------------------------------------------
function invalidateTaskViews(qc: QueryClient, taskId?: string) {
  void qc.invalidateQueries({ queryKey: keys.tasks });
  void qc.invalidateQueries({ queryKey: keys.dashboard });
  if (taskId) void qc.invalidateQueries({ queryKey: keys.task(taskId) });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreate) => api.post<Task>("/tasks", body),
    onSuccess: (task) => {
      invalidateTaskViews(qc, task.id);
      void qc.invalidateQueries({ queryKey: keys.labels });
    },
    onError: (e) => notifyError(e),
  });
}

export function useUpdateTask(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskUpdate) => api.patch<Task>(`/tasks/${id}`, body),
    onSuccess: () => {
      invalidateTaskViews(qc, id);
      void qc.invalidateQueries({ queryKey: keys.labels });
    },
    onError: (e) => notifyError(e),
  });
}

export function useMoveTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; status: TaskStatus; before_id?: string | null; after_id?: string | null; position?: number }) =>
      api.post<Task>(`/tasks/${v.id}/move`, { status: v.status, before_id: v.before_id, after_id: v.after_id }),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: keys.tasks });
      const previous = qc.getQueryData<Task[]>(keys.tasks);
      if (previous) {
        qc.setQueryData<Task[]>(
          keys.tasks,
          previous.map((t) => (t.id === v.id ? { ...t, status: v.status, position: v.position ?? t.position } : t)),
        );
      }
      return { previous };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(keys.tasks, ctx.previous);
      notifyError(e);
    },
    onSettled: (_d, _e, v) => invalidateTaskViews(qc, v.id),
  });
}

export function useArchiveTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/tasks/${id}`),
    onSuccess: (_d, id) => invalidateTaskViews(qc, id),
    onError: (e) => notifyError(e),
  });
}

export function useTaskAction<TBody>(taskId: string, path: string, method: "post" | "delete" = "post") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TBody) =>
      method === "post" ? api.post<unknown>(`/tasks/${taskId}${path}`, body) : api.delete<unknown>(`/tasks/${taskId}${path}`),
    onSuccess: () => invalidateTaskViews(qc, taskId),
    onError: (e) => notifyError(e),
  });
}

export function usePreviewRun(taskId: string) {
  return useMutation({ mutationFn: (body: RunCreate) => api.post<RunPreview>(`/tasks/${taskId}/runs/preview`, body) });
}

export function useStartRun(taskId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: RunCreate; idempotencyKey: string }) =>
      api.post<Run>(`/tasks/${taskId}/runs`, v.body, { "Idempotency-Key": v.idempotencyKey }),
    onSuccess: () => {
      invalidateTaskViews(qc, taskId);
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (e) => notifyError(e),
  });
}

export function useRunAction(runId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { action: string; body?: unknown }) => api.post<Record<string, unknown>>(`/runs/${runId}/${v.action}`, v.body ?? {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.run(runId) });
      void qc.invalidateQueries({ queryKey: ["runs"] });
      void qc.invalidateQueries({ queryKey: keys.tasks });
      void qc.invalidateQueries({ queryKey: keys.diff(runId) });
    },
    onError: (e) => notifyError(e),
  });
}

export function useResolveApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; decision: "approve" | "deny"; message?: string; remember?: boolean }) =>
      api.post<Approval>(`/approvals/${v.id}/resolve`, { decision: v.decision, message: v.message, remember: v.remember ?? false }),
    onSuccess: (a) => {
      void qc.invalidateQueries({ queryKey: keys.approvals });
      void qc.invalidateQueries({ queryKey: keys.run(a.run_id) });
      void qc.invalidateQueries({ queryKey: keys.dashboard });
    },
    onError: (e) => notifyError(e),
  });
}

export function useImportRecords() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { record_ids: string[]; status?: TaskStatus; execution_mode?: string; workspace_id?: string | null }) =>
      api.post<Task[]>("/sources/records/import", body),
    onSuccess: (tasks) => {
      invalidateTaskViews(qc);
      void qc.invalidateQueries({ queryKey: ["records"] });
      toast.success(`Created ${tasks.length} task${tasks.length === 1 ? "" : "s"}`);
    },
    onError: (e) => notifyError(e),
  });
}

export function useDismissRecords() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { record_ids: string[]; dismissed: boolean }) => api.post("/sources/records/dismiss", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["records"] }),
    onError: (e) => notifyError(e),
  });
}

export function useWorkspaceMutations() {
  const qc = useQueryClient();
  const done = () => {
    void qc.invalidateQueries({ queryKey: keys.workspaces });
    void qc.invalidateQueries({ queryKey: ["records"] });
    void qc.invalidateQueries({ queryKey: keys.dashboard });
  };
  return {
    create: useMutation({
      mutationFn: (body: { path: string; name?: string; trusted?: boolean }) => api.post<Workspace>("/workspaces", body),
      onSuccess: (ws) => {
        done();
        toast.success(`Added ${ws.name} — found ${ws.open_items} item${ws.open_items === 1 ? "" : "s"}`);
      },
      onError: (e) => notifyError(e),
    }),
    update: useMutation({
      mutationFn: (v: { id: string; body: Partial<Pick<Workspace, "name" | "trusted" | "scan_settings">> }) =>
        api.patch<Workspace>(`/workspaces/${v.id}`, v.body),
      onSuccess: done,
      onError: (e) => notifyError(e),
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.delete(`/workspaces/${id}`),
      onSuccess: done,
      onError: (e) => notifyError(e),
    }),
    scan: useMutation({
      mutationFn: (id: string) => api.post<{ items: number; created: number; state_changes: number }>(`/workspaces/${id}/scan`),
      onSuccess: (summary) => {
        done();
        toast.success(`Scan complete: ${summary.items} open item${summary.items === 1 ? "" : "s"}, ${summary.created} new`);
      },
      onError: (e) => notifyError(e),
    }),
  };
}

export function useConnectionMutations() {
  const qc = useQueryClient();
  const done = () => {
    void qc.invalidateQueries({ queryKey: keys.connections });
    void qc.invalidateQueries({ queryKey: ["records"] });
  };
  return {
    create: useMutation({
      mutationFn: (body: ConnectionCreate) => api.post<Connection>("/connections", body),
      onSuccess: (c) => {
        done();
        toast.success(`Connected ${c.name}: ${c.open_items} open item${c.open_items === 1 ? "" : "s"}`);
      },
      onError: (e) => notifyError(e),
    }),
    update: useMutation({
      mutationFn: (v: { id: string; body: Record<string, unknown> }) => api.patch<Connection>(`/connections/${v.id}`, v.body),
      onSuccess: done,
      onError: (e) => notifyError(e),
    }),
    remove: useMutation({
      mutationFn: (id: string) => api.delete(`/connections/${id}`),
      onSuccess: done,
      onError: (e) => notifyError(e),
    }),
    sync: useMutation({
      mutationFn: (id: string) => api.post<{ status: string; error: string | null }>(`/connections/${id}/sync`),
      onSuccess: (r) => {
        done();
        if (r.error) toast.warning(r.error);
        else toast.success("Synchronised with GitHub");
      },
      onError: (e) => notifyError(e),
    }),
  };
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AppSettings) => api.put<AppSettings>("/settings", body),
    onSuccess: (s) => {
      qc.setQueryData(keys.settings, s);
      void qc.invalidateQueries({ queryKey: keys.provider });
      void qc.invalidateQueries({ queryKey: keys.meta });
    },
    onError: (e) => notifyError(e),
  });
}

export function useRoleMutations() {
  const qc = useQueryClient();
  const done = () => void qc.invalidateQueries({ queryKey: keys.roles });
  return {
    create: useMutation({ mutationFn: (body: RoleIn) => api.post<Role>("/roles", body), onSuccess: done, onError: (e) => notifyError(e) }),
    update: useMutation({
      mutationFn: (v: { id: string; body: Partial<RoleIn> }) => api.patch<Role>(`/roles/${v.id}`, v.body),
      onSuccess: done,
      onError: (e) => notifyError(e),
    }),
    remove: useMutation({ mutationFn: (id: string) => api.delete(`/roles/${id}`), onSuccess: done, onError: (e) => notifyError(e) }),
  };
}

export function useDemo() {
  const qc = useQueryClient();
  const done = () => void qc.invalidateQueries();
  return {
    seed: useMutation({
      mutationFn: () => api.post<{ created: boolean }>("/demo"),
      onSuccess: (r) => {
        done();
        toast.success(r.created ? "Demo workspace created — two demo runs are starting" : "Demo data is already loaded");
      },
      onError: (e) => notifyError(e),
    }),
    remove: useMutation({
      mutationFn: () => api.delete<{ tasks: number }>("/demo"),
      onSuccess: () => {
        done();
        toast.success("Demo data removed");
      },
      onError: (e) => notifyError(e),
    }),
  };
}

// -- workflows -----------------------------------------------------------------------------
export const useWorkflows = () => useQuery({ queryKey: keys.workflows, queryFn: () => api.get<WorkflowListItem[]>("/workflows") });
export const useWorkflow = (id: string) =>
  useQuery({ queryKey: keys.workflow(id), queryFn: () => api.get<Workflow>(`/workflows/${id}`), retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2 });

export function useCreateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) => api.post<Workflow>("/workflows", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.workflows }),
    onError: (e) => notifyError(e),
  });
}

/** Saves a draft. Errors are left to the caller, which shows them in the editor. */
export function useSaveWorkflow(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkflowUpdate) => api.patch<Workflow>(`/workflows/${id}`, body),
    onSuccess: (saved) => {
      qc.setQueryData(keys.workflow(id), saved);
      void qc.invalidateQueries({ queryKey: keys.workflows });
    },
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/workflows/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.workflows }),
    onError: (e) => notifyError(e),
  });
}

/** Starts a run; errors are shown in the run dialog. */
export function useRunWorkflow(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: WorkflowRunCreate; idempotencyKey: string }) =>
      api.post<Run>(`/workflows/${id}/runs`, v.body, { "Idempotency-Key": v.idempotencyKey }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.tasks });
      void qc.invalidateQueries({ queryKey: ["runs"] });
      void qc.invalidateQueries({ queryKey: keys.workflows });
    },
  });
}
