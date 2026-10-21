import type { components } from "./schema";

type S = components["schemas"];

export type Task = S["TaskOut"];
export type TaskDetail = S["TaskDetail"];
export type TaskCreate = S["TaskCreate"];
export type TaskUpdate = S["TaskUpdate"];
export type TaskRef = S["TaskRef"];
export type Activity = S["ActivityOut"];
export type SourceRef = S["SourceRef"];
export type RunBrief = S["RunBrief"];
export type Run = S["RunOut"];
export type RunListItem = S["RunListItem"];
export type RunCreate = S["RunCreate"];
export type RunPreview = S["RunPreview"];
export type Session = S["SessionOut"];
export type Subtask = S["SubtaskOut"];
export type Artifact = S["ArtifactOut"];
export type Approval = S["ApprovalOut"];
export type Workspace = S["WorkspaceOut"];
export type SourceRecord = S["SourceRecordOut"];
export type SourceRecordPage = S["SourceRecordPage"];
export type Connection = S["ConnectionOut"];
export type ConnectionCreate = S["ConnectionCreate"];
export type GitHubRepo = S["GitHubRepoOut"];
export type Role = S["RoleOut"];
export type Workflow = S["WorkflowOut"];
export type WorkflowListItem = S["WorkflowListItem"];
export type WorkflowStepIn = S["WorkflowStep"];
export type WorkflowIssue = S["Issue"];
export type WorkflowUpdate = S["WorkflowUpdate"];
export type WorkflowRunCreate = S["WorkflowRunCreate"];
export type RoleIn = S["RoleIn"];
export type AppSettings = S["AppSettings-Output"];
export type ProviderStatus = S["ProviderStatusOut"];
export type Dashboard = S["DashboardOut"];
export type Diff = S["DiffOut"];
export type Meta = S["MetaOut"];
export type Browse = S["BrowseOut"];
export type OverviewGraph = S["OverviewGraph"];

export type TaskStatus = "backlog" | "ready" | "in_progress" | "review" | "blocked" | "done";
export type Priority = "urgent" | "high" | "medium" | "low" | "none";
export type ExecutionMode = "single" | "team" | "auto";

export interface ExecutionEvent {
  seq: number;
  id: string;
  run_id: string;
  task_id: string | null;
  session_id: string | null;
  agent_id: string | null;
  parent_agent_id: string | null;
  type: string;
  status: string | null;
  ts: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface GitHubStatus {
  connected: boolean;
  source: string;
  message?: string;
  login?: string;
  name?: string;
  avatar_url?: string;
  scopes?: string[];
  rate_limit?: { limit: number | null; remaining: number | null; reset_at: string | null };
  read_only?: boolean;
}
