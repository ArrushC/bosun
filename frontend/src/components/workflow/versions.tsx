import { BadgeCheck, ChevronRight, CircleDashed, History, PencilLine, Play, RefreshCw, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { usePublishWorkflow, useWorkflowVersions } from "@/api/hooks";
import type { AgentChange, Role, Workflow, WorkflowIssue, WorkflowVersion } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Notice, RoleTag, Spinner } from "@/components/ui/display";
import { Field, Textarea } from "@/components/ui/inputs";
import { Dialog, Tip } from "@/components/ui/overlays";
import { PROFILE_LABEL } from "@/lib/status";
import { cn, plural, relativeTime } from "@/lib/utils";
import { hasChanges, normalizeStep, type SpecChanges, type Step, specChanges } from "@/lib/workflow";

import { agentColor, APPROVAL_COLOR, ApprovalIcon } from "./parts";

export interface DraftContent {
  name: string;
  description: string;
  steps: Step[];
}

export function versionContent(v: Pick<WorkflowVersion, "name" | "description" | "spec">): DraftContent {
  return { name: v.name, description: v.description, steps: (v.spec.steps ?? []).map(normalizeStep) };
}

export function versionPath(workflowId: string, n: number): string {
  return `/workflows/${workflowId}/versions/${n}`;
}

/**
 * Where the draft stands against the latest published version. `changes` is compared locally
 * (so it follows typing at once); the server's flag is the fallback while that version loads.
 */
export function draftState(workflow: Pick<Workflow, "latest_version" | "unpublished_changes">, changes: SpecChanges | null): { changed: boolean; label: string } {
  const latest = workflow.latest_version;
  if (!latest) return { changed: true, label: "Not published yet" };
  const changed = changes ? hasChanges(changes) : workflow.unpublished_changes;
  return { changed, label: changed ? `Changed since version ${latest.number}` : `Same as version ${latest.number}` };
}

/** The draft's state in a sentence: "the draft is …". */
export function draftPhrase(workflow: Pick<Workflow, "latest_version" | "unpublished_changes">, changes: SpecChanges | null): string {
  const latest = workflow.latest_version;
  if (!latest) return "not published yet";
  return draftState(workflow, changes).changed ? `changed since version ${latest.number}` : `the same as version ${latest.number}`;
}

/** The draft's state next to the workflow's name, opening the Versions tab. */
export function DraftBadge({ workflow, changes, onClick }: { workflow: Pick<Workflow, "latest_version" | "unpublished_changes">; changes: SpecChanges | null; onClick: () => void }) {
  const { changed, label } = draftState(workflow, changes);
  const Icon = !workflow.latest_version ? CircleDashed : changed ? PencilLine : BadgeCheck;
  return (
    <button onClick={onClick} className="inline-flex items-center gap-1.5 rounded-control px-1.5 py-0.5 text-sm text-text-3 hover:bg-raised hover:text-text-2">
      <span className="rounded-full bg-raised px-2 text-xs font-medium text-text-2">Draft</span>
      <Icon className="size-4" /> {label}
    </button>
  );
}

/** What changed between two copies of a workflow, as a short list. */
export function ChangeList({ changes, className }: { changes: SpecChanges; className?: string }) {
  const items: Array<[string, string]> = [];
  if (changes.name) items.push(["Renamed", "the workflow"]);
  if (changes.description) items.push(["Changed", "the description"]);
  for (const s of changes.added) items.push(["Added", s.title.trim() || "Untitled step"]);
  for (const s of changes.removed) items.push(["Removed", s.title.trim() || "Untitled step"]);
  for (const c of changes.changed) items.push(["Changed", `${c.title}: ${c.fields.join(", ")}`]);
  if (changes.moved) items.push(["Moved", plural(changes.moved, "step")]);
  if (!items.length) return <p className={cn("text-sm text-text-3", className)}>No changes to the steps.</p>;
  return (
    <ul className={cn("space-y-1.5 text-sm", className)}>
      {items.map(([what, detail], i) => (
        <li key={i} className="flex gap-2">
          <span className="w-20 shrink-0 text-text-3">{what}</span>
          <span className="min-w-0 break-words">{detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** How the agents a version froze differ from today's. */
export function AgentChangeNotes({ changes, version }: { changes: AgentChange[]; version: number }) {
  const changed = changes.filter((c) => c.state === "changed");
  if (!changed.length) return null;
  return (
    <Notice tone="warn">
      Version {version} runs {changed.length === 1 ? "this agent" : "these agents"} as {changed.length === 1 ? "it was" : "they were"} when it was published, not as{" "}
      {changed.length === 1 ? "it is" : "they are"} now:{" "}
      {changed.map((c, i) => (
        <span key={c.key}>
          {i > 0 && "; "}
          <span className="font-medium">{c.name}</span> ({c.fields.join(", ")} changed since)
        </span>
      ))}
      .
    </Notice>
  );
}

/** The agents a set of steps uses, with their access level. */
export function AgentTags({ agents }: { agents: Array<{ key: string; name: string; color?: string | null; permission_profile?: string }> }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {agents.map((a) => (
        <Tip key={a.key} label={a.permission_profile ? (PROFILE_LABEL[a.permission_profile] ?? a.permission_profile) : a.name}>
          <span tabIndex={0} className="rounded-full">
            <RoleTag name={a.name} color={agentColor(a)} />
          </span>
        </Tip>
      ))}
      {agents.length === 0 && <span className="text-text-3">None</span>}
    </span>
  );
}

export function PublishDialog({
  open,
  onOpenChange,
  workflowId,
  revision,
  draft,
  latest,
  roles,
  errors,
  saving,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  /** The saved draft revision being published. */
  revision: number;
  draft: DraftContent;
  /** The latest published version, loaded; null when there is none or it is loading. */
  latest: WorkflowVersion | null;
  roles: Role[];
  errors: WorkflowIssue[];
  saving: boolean;
}) {
  const navigate = useNavigate();
  const publish = usePublishWorkflow(workflowId);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (!open) return;
    setNote("");
    publish.reset();
  }, [open]); // not `publish`: it is a new object on every render

  const next = (latest?.number ?? 0) + 1;
  const changes = useMemo(() => (latest ? specChanges(versionContent(latest), draft) : null), [latest, draft]);
  const agentsChanged = (latest?.agent_changes ?? []).filter((c) => c.state === "changed");
  const nothingNew = !!latest && !!changes && !hasChanges(changes) && agentsChanged.length === 0;
  const used = [...new Set(draft.steps.filter((s) => s.kind === "agent" && s.agent).map((s) => s.agent as string))]
    .map((k) => roles.find((r) => r.key === k))
    .filter((r): r is Role => !!r);
  const blocked = errors.length ? `Fix ${plural(errors.length, "problem")} first` : nothingNew ? `Nothing has changed since version ${latest?.number}` : null;

  const submit = () => {
    if (blocked || saving || publish.isPending) return;
    publish.mutate(
      { revision, note: note.trim() },
      {
        onSuccess: (v) => {
          onOpenChange(false);
          toast.success(`Published version ${v.number}`, {
            description: "Runs of it always use these steps and agents. The draft stays editable.",
            action: { label: "View", onClick: () => navigate(versionPath(workflowId, v.number)) },
          });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Publish version ${next}`}
      description="A version is a fixed copy of this draft and the agents it uses. Runs of it always do the same thing, however the draft changes later."
      footer={
        <>
          {saving && <span className="mr-auto text-sm text-text-3">Saving your latest changes…</span>}
          {!saving && blocked && <span className="mr-auto text-sm text-text-3">{blocked}</span>}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={!!blocked || saving || publish.isPending}>
            {publish.isPending ? <Spinner className="text-accent-fg" /> : <Upload />} Publish version {next}
          </Button>
        </>
      }
    >
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <section className="rounded-[14px] bg-sunken px-5 py-4">
          <h3 className="mb-3 text-sm font-semibold">{latest ? `Changes since version ${latest.number}` : "The first version"}</h3>
          {latest && changes ? (
            <>
              <ChangeList changes={changes} />
              {agentsChanged.length > 0 && (
                <p className="mt-3 text-sm text-text-2">
                  Agents changed since version {latest.number}:{" "}
                  {agentsChanged.map((c) => `${c.name} (${c.fields.join(", ")})`).join("; ")}. This version freezes them as they are now.
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-text-2">
              {plural(draft.steps.length, "step")}, using{" "}
              {used.length ? used.map((r) => r.name).join(", ") : "no agents"}
              {draft.steps.some((s) => s.kind === "approval") ? ", with your approval along the way" : ""}.
            </p>
          )}
        </section>
        {errors.length > 0 && (
          <ErrorNote title={`Fix ${plural(errors.length, "problem")} before publishing`}>
            {errors[0].message}
            {errors.length > 1 ? ` (${errors.length - 1} more)` : ""}
          </ErrorNote>
        )}
        <Field label="Note" hint="Optional. Shown with the version, so you can tell versions apart later.">
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} placeholder="What changed, in a few words" autoFocus />
        </Field>
        <div className="text-sm text-text-2">
          <p className="mb-2 font-medium text-text">Agents frozen into this version</p>
          <AgentTags agents={used} />
        </div>
        {publish.error && <ErrorNote title="Not published">{(publish.error as Error).message}</ErrorNote>}
      </form>
    </Dialog>
  );
}

/** The Versions tab: the draft, then every published version, newest first. */
export function VersionList({
  workflowId,
  workflow,
  changes,
  publishBlocked,
  onEditDraft,
  onPublish,
  onRun,
}: {
  workflowId: string;
  workflow: Pick<Workflow, "latest_version" | "unpublished_changes">;
  changes: SpecChanges | null;
  publishBlocked: string | null;
  onEditDraft: () => void;
  onPublish: () => void;
  onRun: (version: number) => void;
}) {
  const { data: versions, isLoading, error, refetch } = useWorkflowVersions(workflowId);
  const state = draftState(workflow, changes);
  if (isLoading) {
    return (
      <ul aria-label="Loading versions" className="mx-8 my-6 divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
        {[0, 1, 2].map((i) => (
          <li key={i} className="px-5 py-4">
            <span className="block h-4 w-48 animate-pulse rounded-[6px] bg-raised" />
            <span className="mt-2 block h-3.5 w-80 max-w-full animate-pulse rounded-[6px] bg-raised" />
          </li>
        ))}
      </ul>
    );
  }
  if (error) {
    return (
      <div className="px-8 py-6">
        <ErrorNote
          title="Versions could not be loaded"
          action={
            <Button size="sm" variant="secondary" onClick={() => void refetch()}>
              <RefreshCw /> Try again
            </Button>
          }
        >
          {(error as Error).message}
        </ErrorNote>
      </div>
    );
  }
  const publishButton = (
    <Tip label={publishBlocked ?? "Publish the draft as a new version"}>
      <span tabIndex={publishBlocked ? 0 : -1}>
        <Button variant="secondary" onClick={onPublish} disabled={!!publishBlocked}>
          <Upload /> Publish
        </Button>
      </span>
    </Tip>
  );
  if (!versions?.length) {
    return (
      <EmptyState icon={History} title="No versions yet" action={publishButton}>
        Publish the draft to keep a fixed copy of it, with its agents as they are now. You can run a version again and again while you keep editing the draft.
      </EmptyState>
    );
  }
  return (
    <div className="space-y-4 px-8 py-6">
      <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
        <li className="flex flex-wrap items-center gap-4 px-5 py-4">
          <PencilLine className="size-5 shrink-0 text-text-3" />
          <span className="min-w-0 flex-1">
            <span className="block text-base">Draft</span>
            <span className="mt-0.5 block text-sm text-text-2">{state.label}. Editable; runs of the draft say so.</span>
          </span>
          <Button variant="ghost" size="sm" onClick={onEditDraft}>
            Edit
          </Button>
          {state.changed && publishButton}
        </li>
        {versions.map((v, i) => (
          <li key={v.number} className="flex items-center gap-4 hover:bg-raised">
            <Link to={versionPath(workflowId, v.number)} className="flex min-w-0 flex-1 items-center gap-4 py-4 pl-5">
              <BadgeCheck className="size-5 shrink-0 text-text-3" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-base">
                  Version {v.number}
                  {i === 0 && <span className="rounded-full bg-accent-soft px-2 text-xs font-medium text-accent-strong">Latest</span>}
                </span>
                <span className="mt-0.5 block truncate text-sm text-text-2">{v.note || "No note"}</span>
              </span>
              <span className="hidden w-24 shrink-0 text-sm text-text-2 lg:block">{plural(v.steps, "step")}</span>
              <span className="hidden w-24 shrink-0 text-sm text-text-2 lg:block">{plural(v.run_count, "run")}</span>
              <span className="hidden w-40 shrink-0 text-xs text-text-3 xl:block">Published {relativeTime(v.created_at)}</span>
              <ChevronRight className="size-4 shrink-0 text-text-3" />
            </Link>
            <Button variant="ghost" size="sm" className="mr-4" onClick={() => onRun(v.number)} aria-label={`Run version ${v.number}`}>
              <Play /> Run
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One step's settings, read-only: how a published version or a run defines it. */
export function StepFacts({ step, steps, agent }: { step: Step; steps: Step[]; agent?: { name: string; permission_profile?: string } }) {
  const approval = step.kind === "approval";
  const title = (key: string) => steps.find((s) => s.key === key)?.title.trim() || "Untitled step";
  const overrides = [
    step.model && `model ${step.model}`,
    step.max_turns && `${step.max_turns} turns`,
    step.timeout_minutes && `${step.timeout_minutes} min`,
    step.retries != null && plural(step.retries, "retry", "retries"),
  ].filter(Boolean);
  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-3 text-sm">
        <dt className="text-text-3">{approval ? "Kind" : "Agent"}</dt>
        <dd className="min-w-0">
          {approval ? (
            <span className="inline-flex items-center gap-1.5">
              <ApprovalIcon className="size-4" style={{ color: APPROVAL_COLOR }} /> Approval by you
            </span>
          ) : (
            <>
              {agent?.name ?? step.agent ?? "None"}
              {agent?.permission_profile && <span className="block text-xs text-text-3">{PROFILE_LABEL[agent.permission_profile] ?? agent.permission_profile}</span>}
            </>
          )}
        </dd>
        <dt className="text-text-3">{approval ? "Asks after" : "Runs after"}</dt>
        <dd>{step.after.length ? step.after.map(title).join(", ") : approval ? "As soon as the run starts" : "Starts with the run"}</dd>
        {!approval && (
          <>
            <dt className="text-text-3">Changes code</dt>
            <dd>{step.changes_code ? "Yes, in its own worktree" : "No"}</dd>
            <dt className="text-text-3">Settings</dt>
            <dd>{overrides.length ? overrides.join(", ") : "Agent defaults"}</dd>
          </>
        )}
      </dl>
      <div>
        <p className="mb-1.5 text-sm font-medium">{approval ? "What to check" : "Instructions"}</p>
        <p className="text-sm leading-6 whitespace-pre-wrap text-text-2">{step.instructions.trim() || "None"}</p>
      </div>
    </div>
  );
}
