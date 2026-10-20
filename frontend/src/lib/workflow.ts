import type { Run } from "@/api/types";

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
