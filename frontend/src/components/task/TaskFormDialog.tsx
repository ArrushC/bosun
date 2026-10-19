import { Link2, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useCreateTask, useTasks, useUpdateTask, useWorkspaces } from "@/api/hooks";
import type { ExecutionMode, Priority, TaskDetail, TaskStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { PriorityIcon } from "@/components/ui/display";
import { Field, Input, NativeSelect, Segmented } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { KIND_LABEL, PRIORITY, TASK_COLUMNS } from "@/lib/status";

import { MarkdownEditor } from "./MarkdownEditor";

interface Attachment {
  type: "file" | "url" | "note";
  value: string;
  label?: string | null;
}

interface FormState {
  title: string;
  description: string;
  status: TaskStatus;
  priority: Priority;
  kind: string;
  labels: string[];
  workspace_id: string | null;
  acceptance_criteria: string;
  agent_instructions: string;
  execution_mode: ExecutionMode;
  attachments: Attachment[];
  depends_on: string[];
}

function initial(task?: TaskDetail, defaults?: Partial<FormState>): FormState {
  return {
    title: task?.title ?? "",
    description: task?.description ?? "",
    status: (task?.status as TaskStatus) ?? "backlog",
    priority: (task?.priority as Priority) ?? "none",
    kind: task?.kind ?? "implementation",
    labels: task?.labels ?? [],
    workspace_id: task?.workspace_id ?? null,
    acceptance_criteria: task?.acceptance_criteria ?? "",
    agent_instructions: task?.agent_instructions ?? "",
    execution_mode: (task?.execution_mode as ExecutionMode) ?? "single",
    attachments: (task?.attachments as Attachment[] | undefined) ?? [],
    depends_on: task?.depends_on ?? [],
    ...defaults,
  };
}

export function TaskFormDialog({ open, onOpenChange, task, defaults }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  task?: TaskDetail;
  defaults?: Partial<FormState>;
}) {
  const { data: workspaces = [] } = useWorkspaces();
  const { data: tasks = [] } = useTasks();
  const create = useCreateTask();
  const update = useUpdateTask(task?.id ?? "");
  const [form, setForm] = useState<FormState>(() => initial(task, defaults));
  const [labelDraft, setLabelDraft] = useState("");
  const [attachDraft, setAttachDraft] = useState("");

  useEffect(() => {
    if (open) {
      const next = initial(task, defaults);
      if (!task && !next.workspace_id && workspaces.length === 1) next.workspace_id = workspaces[0].id;
      setForm(next);
      setLabelDraft("");
      setAttachDraft("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, task?.id]);

  // Default to the only workspace once the list has loaded (new tasks only).
  useEffect(() => {
    if (open && !task && workspaces.length === 1) {
      setForm((f) => (f.workspace_id ? f : { ...f, workspace_id: workspaces[0].id }));
    }
  }, [open, task, workspaces]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const dependencyOptions = useMemo(() => tasks.filter((t) => t.id !== task?.id && !form.depends_on.includes(t.id)), [tasks, task?.id, form.depends_on]);

  const addLabel = () => {
    const parts = labelDraft.split(",").map((l) => l.trim().toLowerCase()).filter(Boolean);
    if (parts.length) set("labels", [...new Set([...form.labels, ...parts])]);
    setLabelDraft("");
  };

  const addAttachment = () => {
    const value = attachDraft.trim();
    if (!value) return;
    const type: Attachment["type"] = /^https?:\/\//.test(value) ? "url" : "file";
    set("attachments", [...form.attachments, { type, value }]);
    setAttachDraft("");
  };

  const submit = async (e?: { preventDefault: () => void }) => {
    e?.preventDefault();
    if (!form.title.trim()) return;
    if (task) {
      const { depends_on: _deps, status: _status, ...rest } = form;
      await update.mutateAsync({ ...rest, kind: rest.kind as TaskDetail["kind"] } as never);
    } else {
      await create.mutateAsync(form as never);
    }
    onOpenChange(false);
  };

  const busy = create.isPending || update.isPending;
  const workspaceMissing = !form.workspace_id;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={task ? `Edit ${task.key}` : "New task"}
      description={task ? undefined : "Describe the work. Agents only start when you press Run with agent."}
      footer={
        <>
          <span className="mr-auto text-xs text-text-3">{workspaceMissing ? "Assign a workspace so agents know where to work." : "Markdown is supported."}</span>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!form.title.trim() || busy} onClick={() => void submit()}>
            {task ? "Save changes" : "Create task"}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="grid gap-4">
        <Input
          autoFocus
          aria-label="Title"
          placeholder="Title"
          value={form.title}
          onChange={(e) => set("title", e.target.value)}
          className="h-10 text-md font-medium"
        />
        <MarkdownEditor label="Description" value={form.description} onChange={(v) => set("description", v)} placeholder="What needs to happen, and why?" rows={6} />

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Workspace">
            <NativeSelect value={form.workspace_id ?? ""} onChange={(e) => set("workspace_id", e.target.value || null)}>
              <option value="">No workspace</option>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Priority">
            <div className="relative">
              <NativeSelect value={form.priority} onChange={(e) => set("priority", e.target.value as Priority)} className="pl-8">
                {Object.entries(PRIORITY).map(([value, p]) => (
                  <option key={value} value={value}>
                    {p.label}
                  </option>
                ))}
              </NativeSelect>
              <PriorityIcon priority={form.priority} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2" />
            </div>
          </Field>
          <Field label="Type">
            <NativeSelect value={form.kind} onChange={(e) => set("kind", e.target.value)}>
              {Object.entries(KIND_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {!task && (
            <Field label="Column">
              <NativeSelect value={form.status} onChange={(e) => set("status", e.target.value as TaskStatus)}>
                {TASK_COLUMNS.filter((c) => c.id !== "done").map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
        </div>

        <Field label="Execution" hint="Single agent is the default; a team adds a coordinator that plans and delegates.">
          <Segmented
            value={form.execution_mode}
            onChange={(v) => set("execution_mode", v)}
            options={[
              { value: "single", label: "Single agent" },
              { value: "team", label: "Agent team" },
              { value: "auto", label: "Let the coordinator decide" },
            ]}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <MarkdownEditor label="Acceptance criteria" value={form.acceptance_criteria} onChange={(v) => set("acceptance_criteria", v)} placeholder="- Tests cover the new behaviour" rows={4} />
          <MarkdownEditor label="Instructions for agents" value={form.agent_instructions} onChange={(v) => set("agent_instructions", v)} placeholder="Constraints, approach, files to avoid…" rows={4} />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Labels">
            <div className="flex flex-wrap items-center gap-1.5 rounded-control border border-line bg-sunken px-1.5 py-1">
              {form.labels.map((l) => (
                <span key={l} className="inline-flex h-5 items-center gap-1 rounded-[4px] bg-raised px-1.5 text-xs">
                  {l}
                  <button type="button" aria-label={`Remove ${l}`} onClick={() => set("labels", form.labels.filter((x) => x !== l))}>
                    <X className="size-3 text-text-3 hover:text-text" />
                  </button>
                </span>
              ))}
              <input
                value={labelDraft}
                onChange={(e) => setLabelDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addLabel();
                  }
                }}
                onBlur={addLabel}
                placeholder={form.labels.length ? "" : "api, backend"}
                aria-label="Add label"
                className="h-6 min-w-24 flex-1 bg-transparent px-1 text-sm focus:outline-none"
              />
            </div>
          </Field>
          <Field label="Depends on">
            <div className="flex flex-col gap-1.5">
              {form.depends_on.map((id) => {
                const dep = tasks.find((t) => t.id === id);
                return (
                  <span key={id} className="flex items-center gap-2 text-sm">
                    <Link2 className="size-3.5 text-text-3" />
                    <span className="mono text-text-3">{dep?.key}</span>
                    <span className="flex-1 truncate">{dep?.title ?? id}</span>
                    {!task && (
                      <button type="button" aria-label="Remove dependency" onClick={() => set("depends_on", form.depends_on.filter((d) => d !== id))}>
                        <X className="size-3.5 text-text-3 hover:text-text" />
                      </button>
                    )}
                  </span>
                );
              })}
              {!task && (
                <NativeSelect value="" onChange={(e) => e.target.value && set("depends_on", [...form.depends_on, e.target.value])}>
                  <option value="">Add a dependency…</option>
                  {dependencyOptions.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.key} {t.title}
                    </option>
                  ))}
                </NativeSelect>
              )}
              {task && <span className="text-2xs text-text-3">Manage dependencies from the task panel.</span>}
            </div>
          </Field>
        </div>

        <Field label="Relevant files and links">
          <div className="flex flex-col gap-1.5">
            {form.attachments.map((a, i) => (
              <span key={`${a.value}-${i}`} className="flex items-center gap-2 text-sm">
                <span className="mono flex-1 truncate text-text-2">{a.value}</span>
                <button type="button" aria-label="Remove attachment" onClick={() => set("attachments", form.attachments.filter((_, j) => j !== i))}>
                  <X className="size-3.5 text-text-3 hover:text-text" />
                </button>
              </span>
            ))}
            <div className="flex gap-2">
              <Input
                value={attachDraft}
                onChange={(e) => setAttachDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addAttachment();
                  }
                }}
                placeholder="src/orders/service.py or https://…"
                aria-label="Add file or link"
              />
              <Button size="md" variant="outline" onClick={addAttachment} aria-label="Add attachment">
                <Plus />
              </Button>
            </div>
          </div>
        </Field>
      </form>
    </Dialog>
  );
}
