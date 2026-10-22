import { ChevronRight, Plus, RefreshCw, Workflow } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { useCreateWorkflow, useWorkflows } from "@/api/hooks";
import type { WorkflowTemplate } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, StatusPill } from "@/components/ui/display";
import { NewWorkflowDialog, TemplateGallery } from "@/components/workflow/templates";
import { RUN_LABEL, runState } from "@/lib/status";
import { plural, relativeTime } from "@/lib/utils";

export function WorkflowsPage() {
  const { data: workflows, isLoading, error, refetch } = useWorkflows();
  const create = useCreateWorkflow();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<{ open: boolean; template: WorkflowTemplate | null }>({ open: false, template: null });

  const blank = () => create.mutate({ name: "Untitled workflow" }, { onSuccess: (w) => navigate(`/workflows/${w.id}?new=1`) });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Workflows"
        actions={
          <Button variant="primary" onClick={() => setDialog({ open: true, template: null })} disabled={create.isPending}>
            <Plus /> New workflow
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-8 pb-12">
        {isLoading && (
          <ul aria-label="Loading workflows" className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
            {[0, 1, 2].map((i) => (
              <li key={i} className="flex items-center gap-4 px-5 py-4">
                <span className="size-5 animate-pulse rounded-[6px] bg-raised" />
                <span className="flex-1 space-y-2">
                  <span className="block h-4 w-56 animate-pulse rounded-[6px] bg-raised" />
                  <span className="block h-3.5 w-96 max-w-full animate-pulse rounded-[6px] bg-raised" />
                </span>
              </li>
            ))}
          </ul>
        )}
        {error && (
          <ErrorNote
            title="Workflows could not be loaded"
            action={
              <Button size="sm" variant="secondary" onClick={() => void refetch()}>
                <RefreshCw /> Try again
              </Button>
            }
          >
            {(error as Error).message}
          </ErrorNote>
        )}
        {workflows && workflows.length === 0 && (
          <>
            <EmptyState icon={Workflow} title="No workflows yet" className="pt-12 pb-10">
              A workflow is a plan of agent steps you can run again and again. Run it on a board task, and each step's agent builds on the
              reports of the steps before it.
            </EmptyState>
            <section aria-labelledby="templates-heading">
              <h2 id="templates-heading" className="text-md font-semibold">
                Start from a template
              </h2>
              <TemplateGallery onPick={(t) => setDialog({ open: true, template: t })} onBlank={blank} busy={create.isPending} />
            </section>
          </>
        )}
        {workflows && workflows.length > 0 && (
          <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
            {workflows.map((w) => (
              <li key={w.id}>
                <Link to={`/workflows/${w.id}`} className="flex items-center gap-4 px-5 py-4 hover:bg-raised">
                  <Workflow className="size-5 shrink-0 text-text-3" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base">{w.name}</span>
                    <span className="mt-0.5 block truncate text-sm text-text-2">{w.description || "No description"}</span>
                  </span>
                  <span className="hidden w-20 shrink-0 text-sm text-text-2 lg:block">{plural(w.steps, "step")}</span>
                  <span className="hidden w-44 shrink-0 text-sm lg:block">
                    {w.latest_version ? <span className="text-text-2">Version {w.latest_version.number}</span> : <span className="text-text-3">Not published</span>}
                    {w.latest_version && w.unpublished_changes && <span className="block text-xs text-text-3">Draft has changes</span>}
                  </span>
                  <span className="hidden w-40 shrink-0 text-xs text-text-3 xl:block">Edited {relativeTime(w.updated_at)}</span>
                  <span className="flex w-44 shrink-0 items-center justify-end gap-2 text-xs text-text-3 xl:w-72">
                    {w.last_run ? (
                      <>
                        <span className="truncate">
                          {w.last_run.task_key}
                          <span className="hidden xl:inline">, {relativeTime(w.last_run.created_at)}</span>
                        </span>
                        <StatusPill state={runState(w.last_run.status)} label={RUN_LABEL[w.last_run.status]} />
                      </>
                    ) : (
                      "Never run"
                    )}
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-text-3" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
      <NewWorkflowDialog open={dialog.open} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} template={dialog.template} />
    </div>
  );
}
