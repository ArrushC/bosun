import { ArrowLeft, Bot, CircleCheck, CircleDashed, LayoutTemplate, OctagonAlert, PenLine, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { useCreateWorkflow, useRoles, useWorkflowTemplates } from "@/api/hooks";
import type { WorkflowTemplate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { ErrorNote, Spinner } from "@/components/ui/display";
import { Field, Input } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { cn, plural } from "@/lib/utils";
import { executionOrder, normalizeStep } from "@/lib/workflow";

import { agentColor, APPROVAL_COLOR, ApprovalIcon } from "./parts";

type SetupItem = WorkflowTemplate["setup"][number];

function needsSetup(t: WorkflowTemplate): SetupItem[] {
  return t.setup.filter((i) => i.level === "required" && !i.done);
}

/** Template cards and a blank workflow, as the empty Workflows page and the New workflow dialog show them. */
export function TemplateGallery({ onPick, onBlank, busy, className }: { onPick: (t: WorkflowTemplate) => void; onBlank: () => void; busy?: boolean; className?: string }) {
  const { data: templates, isLoading, error, refetch } = useWorkflowTemplates();
  const card = "flex min-h-36 flex-col items-start gap-2 rounded-panel p-5 text-left transition-colors disabled:opacity-60";
  return (
    <div className={className}>
      {error && (
        <ErrorNote
          title="Templates could not be loaded"
          action={
            <Button size="sm" variant="secondary" onClick={() => void refetch()}>
              <RefreshCw /> Try again
            </Button>
          }
        >
          {(error as Error).message}. You can still start from a blank workflow.
        </ErrorNote>
      )}
      <ul className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Templates">
        {isLoading &&
          [0, 1, 2].map((i) => (
            <li key={i} className={cn(card, "bg-panel")} aria-hidden>
              <span className="size-5 animate-pulse rounded-[6px] bg-raised" />
              <span className="h-4 w-32 animate-pulse rounded-[6px] bg-raised" />
              <span className="h-3.5 w-48 animate-pulse rounded-[6px] bg-raised" />
            </li>
          ))}
        {templates?.map((t) => {
          const missing = needsSetup(t);
          return (
            <li key={t.key}>
              <button className={cn(card, "h-full w-full bg-panel hover:bg-raised")} onClick={() => onPick(t)} disabled={busy}>
                <LayoutTemplate className="size-5 text-text-3" />
                <span className="text-base font-semibold">{t.name}</span>
                <span className="text-sm leading-6 text-text-2">{t.outline}</span>
                {missing.length > 0 && (
                  <span className="mt-auto inline-flex items-center gap-1.5 pt-1 text-xs text-st-approval">
                    <OctagonAlert className="size-3.5" /> {plural(missing.length, "thing")} to set up first
                  </span>
                )}
              </button>
            </li>
          );
        })}
        <li>
          <button className={cn(card, "h-full w-full border border-dashed border-line hover:bg-raised")} onClick={onBlank} disabled={busy}>
            <Plus className="size-5 text-text-3" />
            <span className="text-base font-semibold">Blank workflow</span>
            <span className="text-sm leading-6 text-text-2">Start with an empty canvas</span>
          </button>
        </li>
      </ul>
    </div>
  );
}

const FORM_ID = "new-workflow-from-template";

/** A template's steps and what to set up, with a name, before creating a draft from it. */
function TemplatePreview({
  template,
  name,
  onNameChange,
  onBack,
  onSubmit,
}: {
  template: WorkflowTemplate;
  name: string;
  onNameChange: (name: string) => void;
  onBack?: () => void;
  onSubmit: () => void;
}) {
  const { data: roles = [] } = useRoles();
  const steps = executionOrder((template.spec.steps ?? []).map(normalizeStep));
  const title = (key: string) => steps.find((s) => s.key === key)?.title ?? key;
  return (
    <form
      id={FORM_ID}
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      {onBack && (
        <Button type="button" variant="ghost" size="sm" className="-mt-2 -ml-2" onClick={onBack}>
          <ArrowLeft /> All templates
        </Button>
      )}
      <p className="text-sm leading-6 text-text-2">{template.description}</p>
      <Field label="Name">
        <Input value={name} onChange={(e) => onNameChange(e.target.value)} maxLength={120} autoFocus aria-invalid={!name.trim()} />
      </Field>
      <section>
        <h3 className="mb-2 text-sm font-semibold">Steps</h3>
        <ol className="divide-y divide-line-soft overflow-hidden rounded-[12px] border border-line-soft">
          {steps.map((s, i) => {
            const role = roles.find((r) => r.key === s.agent);
            const approval = s.kind === "approval";
            return (
              <li key={s.key} className="flex items-start gap-3 px-4 py-3">
                <span className="w-5 shrink-0 pt-0.5 text-xs text-text-3 tabular-nums">{i + 1}</span>
                {approval ? (
                  <ApprovalIcon className="mt-0.5 size-4 shrink-0" style={{ color: APPROVAL_COLOR }} />
                ) : (
                  <Bot className="mt-0.5 size-4 shrink-0" style={{ color: agentColor(role ?? (s.agent ? { key: s.agent } : undefined)) }} />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{s.title}</span>
                  <span className="block text-xs text-text-3">
                    {approval ? "Approval by you" : (role?.name ?? s.agent)}
                    {s.after.length ? `, after ${s.after.map(title).join(" and ")}` : ", starts the run"}
                  </span>
                </span>
                {s.changes_code && (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-[6px] bg-raised px-1.5 text-2xs text-text-2">
                    <PenLine className="size-3" /> Edits code
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </section>
      <section>
        <h3 className="mb-2 text-sm font-semibold">Before you run it</h3>
        <ul className="space-y-2">
          {template.setup.map((item, i) => (
            <li key={i} className="flex items-start gap-2.5 text-sm">
              {item.done ? (
                <CircleCheck className="mt-0.5 size-4 shrink-0 text-st-ok" aria-label="Done" />
              ) : item.level === "required" ? (
                <OctagonAlert className="mt-0.5 size-4 shrink-0 text-st-approval" aria-label="Needed" />
              ) : (
                <CircleDashed className="mt-0.5 size-4 shrink-0 text-text-3" aria-label="Recommended" />
              )}
              <span className={cn("min-w-0 flex-1", item.done ? "text-text-2" : "text-text")}>
                {item.message}
                {!item.done && item.level === "recommended" && <span className="text-text-3"> (recommended)</span>}
                {!item.done && item.link && (
                  <>
                    {" "}
                    <Link to={item.link} className="text-accent-strong underline underline-offset-[3px]">
                      Open {item.link === "/settings" ? "Settings" : "Integrations"}
                    </Link>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs leading-5 text-text-3">
          The new workflow is your own copy: change any step, agent or instruction. Each run also needs a board task that says what to work on.
        </p>
      </section>
    </form>
  );
}

/** New workflow: choose a template or a blank canvas. Opens straight on a template's preview when given one. */
export function NewWorkflowDialog({ open, onOpenChange, template }: { open: boolean; onOpenChange: (open: boolean) => void; template?: WorkflowTemplate | null }) {
  const navigate = useNavigate();
  const blank = useCreateWorkflow();
  const create = useCreateWorkflow();
  const [picked, setPicked] = useState<WorkflowTemplate | null>(template ?? null);
  const [name, setName] = useState("");
  const pick = (t: WorkflowTemplate | null) => {
    setPicked(t);
    setName(t?.name ?? "");
    create.reset();
  };
  useEffect(() => {
    if (open) pick(template ?? null);
  }, [open, template]); // not `pick`: it is a new function on every render
  const createBlank = () => blank.mutate({ name: "Untitled workflow" }, { onSuccess: (w) => navigate(`/workflows/${w.id}?new=1`) });
  const submit = () => {
    if (!picked || !name.trim() || create.isPending) return;
    create.mutate({ name: name.trim(), template: picked.key }, { onSuccess: (w) => navigate(`/workflows/${w.id}`) });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size={picked ? "md" : "xl"}
      title={picked ? `New workflow from ${picked.name}` : "New workflow"}
      description={picked ? undefined : "Start from a template and adapt it, or from a blank canvas."}
      footer={
        picked && (
          <>
            {create.error && <span className="mr-auto text-sm text-st-fail">Not created: {(create.error as Error).message}</span>}
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" form={FORM_ID} variant="primary" disabled={!name.trim() || create.isPending}>
              {create.isPending ? <Spinner className="text-accent-fg" /> : <Plus />} Create workflow
            </Button>
          </>
        )
      }
    >
      {picked ? (
        <TemplatePreview template={picked} name={name} onNameChange={setName} onBack={template ? undefined : () => pick(null)} onSubmit={submit} />
      ) : (
        <TemplateGallery onPick={pick} onBlank={createBlank} busy={blank.isPending} className="-mt-4" />
      )}
    </Dialog>
  );
}
