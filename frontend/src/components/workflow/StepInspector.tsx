import { Bot, ChevronDown, ChevronRight, Copy, GitMerge, Plus, Trash2, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import type { Role, WorkflowIssue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Field, Input, NativeSelect, Segmented, Switch, Textarea } from "@/components/ui/inputs";
import { DropdownContent, DropdownItem, DropdownMenu, DropdownTrigger, Tip } from "@/components/ui/overlays";
import { PROFILE_DESCRIPTION } from "@/lib/status";
import { cn } from "@/lib/utils";
import { MAX_STEPS, type Step, stepTitle, upstream } from "@/lib/workflow";

import { AgentMenu, agentColor, IssueIcon } from "./parts";

const MODELS = [
  ["", "The agent's default"],
  ["sonnet", "Sonnet"],
  ["opus", "Opus"],
  ["haiku", "Haiku"],
] as const;

function FieldIssues({ issues }: { issues: WorkflowIssue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="space-y-1">
      {issues.map((i) => (
        <li key={i.code + i.message} className={cn("flex items-start gap-1.5 text-xs", i.level === "error" ? "text-st-fail" : "text-st-approval")}>
          <IssueIcon level={i.level} className="mt-px size-3.5" />
          {i.message}
        </li>
      ))}
    </ul>
  );
}

function Panel({ label, overlay, onClose, children }: { label: string; overlay?: boolean; onClose?: () => void; children: ReactNode }) {
  return (
    <aside
      aria-label={label}
      className={cn(
        "flex w-[380px] shrink-0 flex-col overflow-y-auto border-l border-line-soft bg-panel",
        overlay && "absolute top-3 right-3 bottom-3 z-20 w-[min(360px,calc(100%-24px))] rounded-panel border shadow-pop",
      )}
    >
      {overlay && onClose && (
        <Button variant="ghost" size="icon-sm" className="absolute top-4 right-4" onClick={onClose} aria-label="Hide the settings panel">
          <X className="size-4" />
        </Button>
      )}
      {children}
    </aside>
  );
}

export interface StepInspectorProps {
  step: Step;
  steps: Step[];
  roles: Role[];
  agents: Role[];
  issues: WorkflowIssue[];
  overlay?: boolean;
  onClose?: () => void;
  onEdit: (patch: Partial<Step>, opts?: { history?: boolean }) => void;
  onCheckpoint: () => void;
  /** A step just added: put focus in its name, ready to type over. */
  focusName?: boolean;
  onRemoveAfter: (key: string) => void;
  onWaitFor: (key: string) => void;
  onAddAfter: (agent: Role) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

export function StepInspector({ step, steps, roles, agents, issues, overlay, onClose, onEdit, onCheckpoint, focusName, onRemoveAfter, onWaitFor, onAddAfter, onDuplicate, onDelete }: StepInspectorProps) {
  const [advanced, setAdvanced] = useState(step.model != null || step.max_turns != null || step.timeout_minutes != null || step.retries != null);
  const role = roles.find((r) => r.key === step.agent);
  const usable = role && role.enabled && !role.is_coordinator;
  const mine = issues.filter((i) => i.steps?.includes(step.key));
  const forField = (field: string) => mine.filter((i) => i.field === field);
  const general = mine.filter((i) => !i.field || !["title", "agent", "instructions", "changes_code", "after"].includes(i.field));
  // Steps this one could also wait for: not itself, none it already waits for (even through
  // others), and none that wait for it.
  const waited = upstream(steps, step.key);
  const waitable = steps.filter((s) => s.key !== step.key && !waited.has(s.key) && !upstream(steps, s.key).has(step.key));
  const overrides = [step.model && `model ${step.model}`, step.max_turns && `${step.max_turns} turns`, step.timeout_minutes && `${step.timeout_minutes} min`, step.retries != null && `${step.retries} ${step.retries === 1 ? "retry" : "retries"}`].filter(Boolean);
  // Text fields: one undo step per field visit, not per keystroke.
  const text = { onFocus: onCheckpoint };
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!focusName) return;
    // Wait for the menu that added the step to close: until then it keeps focus to itself.
    const t = setTimeout(() => {
      nameRef.current?.focus();
      nameRef.current?.select();
    });
    return () => clearTimeout(t);
  }, [focusName]);

  return (
    <Panel label={`${step.title || "Step"} settings`} overlay={overlay} onClose={onClose}>
      <div className={cn("flex items-center gap-3 px-6 pt-5 pb-4", overlay && "pr-14")}>
        <Bot className="size-5 shrink-0" style={{ color: agentColor(usable ? role : undefined) }} />
        <h2 className="min-w-0 flex-1 truncate text-md font-semibold">{step.title.trim() || "Untitled step"}</h2>
        <Tip label="Duplicate step">
          <Button variant="ghost" size="icon-sm" onClick={onDuplicate} disabled={steps.length >= MAX_STEPS} aria-label="Duplicate step">
            <Copy className="size-4" />
          </Button>
        </Tip>
        <Tip label="Delete step (Delete)">
          <Button variant="ghost" size="icon-sm" onClick={onDelete} aria-label="Delete step">
            <Trash2 className="size-4" />
          </Button>
        </Tip>
      </div>
      <div className="space-y-6 px-6 pb-8">
        <FieldIssues issues={general} />
        <div className="space-y-2">
          <Field label="Name">
            <Input ref={nameRef} value={step.title} maxLength={120} {...text} onChange={(e) => onEdit({ title: e.target.value }, { history: false })} aria-invalid={forField("title").some((i) => i.level === "error")} />
          </Field>
          <FieldIssues issues={forField("title")} />
        </div>
        <div className="space-y-2">
          <Field label="Agent" hint={usable ? PROFILE_DESCRIPTION[role.permission_profile] : undefined}>
            <NativeSelect
              value={step.agent ?? ""}
              onChange={(e) => {
                const next = roles.find((r) => r.key === e.target.value);
                onEdit({ agent: e.target.value || null, ...(next && next.permission_profile === "read_only" ? { changes_code: false } : {}) });
              }}
              aria-invalid={forField("agent").some((i) => i.level === "error")}
              className={cn(forField("agent").some((i) => i.level === "error") && "border-st-fail")}
            >
              {!usable && <option value={step.agent ?? ""}>{step.agent ? `${role?.name ?? step.agent} (not available)` : "Choose an agent"}</option>}
              {agents.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <FieldIssues issues={forField("agent")} />
        </div>
        <div className="space-y-2">
          <Field label="Instructions" hint="The agent also sees the task's title and description, and the reports of the steps it runs after.">
            <Textarea value={step.instructions} rows={5} maxLength={8000} {...text} onChange={(e) => onEdit({ instructions: e.target.value }, { history: false })} placeholder="What should this agent do in this step?" />
          </Field>
          <FieldIssues issues={forField("instructions")} />
        </div>
        <div className="space-y-2">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <p id={`changes-${step.key}`} className="text-sm font-medium">Changes code</p>
              <p className="mt-1 text-xs text-text-3">
                {usable && role.permission_profile === "read_only"
                  ? `${role.name} can only read files, so this step can't change code.`
                  : "Works in its own worktree. Its changes are merged before the steps after it start."}
              </p>
            </div>
            <Switch checked={step.changes_code} onCheckedChange={(v) => onEdit({ changes_code: v })} label="Changes code" disabled={!!usable && role.permission_profile === "read_only" && !step.changes_code} />
          </div>
          <FieldIssues issues={forField("changes_code")} />
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium">Runs after</p>
          <div className="flex flex-wrap items-center gap-2">
            {step.after.length === 0 && <span className="text-sm text-text-3">Starts with the run</span>}
            {step.after.map((key) => (
              <span key={key} className="inline-flex h-8 items-center gap-1 rounded-control bg-raised pr-1 pl-3 text-sm">
                {stepTitle(steps, key)}
                <button className="rounded-[6px] p-1 text-text-3 hover:bg-line-soft hover:text-text" onClick={() => onRemoveAfter(key)} aria-label={`Stop waiting for ${stepTitle(steps, key)}`}>
                  <X className="size-3.5" />
                </button>
              </span>
            ))}
          </div>
          <FieldIssues issues={forField("after")} />
          <div className="-ml-2 flex flex-wrap gap-1">
            <DropdownMenu>
              {waitable.length ? (
                <DropdownTrigger asChild>
                  <Button size="sm" variant="ghost">
                    <GitMerge /> Wait for another step
                  </Button>
                </DropdownTrigger>
              ) : (
                <Tip label="Every other step already runs before or after this one.">
                  <span tabIndex={0} className="rounded-control">
                    <Button size="sm" variant="ghost" disabled>
                      <GitMerge /> Wait for another step
                    </Button>
                  </span>
                </Tip>
              )}
              <DropdownContent align="start">
                {waitable.map((s) => (
                  <DropdownItem key={s.key} onSelect={() => onWaitFor(s.key)}>
                    {stepTitle(steps, s.key)}
                  </DropdownItem>
                ))}
              </DropdownContent>
            </DropdownMenu>
            <AgentMenu agents={agents} onPick={onAddAfter}>
              <Button size="sm" variant="ghost" disabled={steps.length >= MAX_STEPS}>
                <Plus /> Add a step after this one
              </Button>
            </AgentMenu>
          </div>
        </div>
        <section className="rounded-[12px] border border-line-soft">
          <button className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)}>
            {advanced ? <ChevronDown className="size-4 text-text-3" /> : <ChevronRight className="size-4 text-text-3" />}
            Advanced
            {!advanced && <span className="ml-auto truncate text-xs font-normal text-text-3">{overrides.length ? overrides.join(", ") : "Agent defaults"}</span>}
          </button>
          {advanced && (
            <div className="space-y-5 px-4 pb-5">
              <Field label="Model" hint="Leave on the agent's default unless this step needs a more or less capable model.">
                <NativeSelect value={step.model ?? ""} onChange={(e) => onEdit({ model: e.target.value || null })}>
                  {MODELS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                  {step.model && !MODELS.some(([v]) => v === step.model) && <option value={step.model}>{step.model}</option>}
                </NativeSelect>
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Max turns">
                  <Input
                    type="number"
                    min={1}
                    max={500}
                    inputMode="numeric"
                    placeholder={String(role?.max_turns ?? "Default")}
                    value={step.max_turns ?? ""}
                    onChange={(e) => onEdit({ max_turns: e.target.value ? Math.min(500, Math.max(1, Number(e.target.value))) : null })}
                  />
                </Field>
                <Field label="Time limit (min)">
                  <Input
                    type="number"
                    min={1}
                    max={600}
                    inputMode="numeric"
                    placeholder="Default"
                    value={step.timeout_minutes ?? ""}
                    onChange={(e) => onEdit({ timeout_minutes: e.target.value ? Math.min(600, Math.max(1, Number(e.target.value))) : null })}
                  />
                </Field>
              </div>
              <Field label="Retries" hint="Extra tries after a failure, each starting the step again. Sign-in, usage-limit and billing errors are never retried. Default follows Settings.">
                <Segmented
                  value={step.retries == null ? "default" : String(step.retries)}
                  onChange={(v) => onEdit({ retries: v === "default" ? null : Number(v) })}
                  options={[
                    { value: "default", label: "Default" },
                    { value: "0", label: "None" },
                    { value: "1", label: "1" },
                    { value: "2", label: "2" },
                  ]}
                />
              </Field>
            </div>
          )}
        </section>
      </div>
    </Panel>
  );
}

export function WorkflowDetails({
  name,
  description,
  stepCount,
  issues,
  overlay,
  onClose,
  autoFocus,
  onEdit,
  onCheckpoint,
}: {
  name: string;
  description: string;
  stepCount: number;
  issues: WorkflowIssue[];
  overlay?: boolean;
  onClose?: () => void;
  autoFocus?: boolean;
  onEdit: (patch: { name?: string; description?: string }) => void;
  onCheckpoint: () => void;
}) {
  const general = issues.filter((i) => !i.steps?.length);
  return (
    <Panel label="Workflow details" overlay={overlay} onClose={onClose}>
      <div className={cn("px-6 pt-5 pb-4", overlay && "pr-14")}>
        <h2 className="text-md font-semibold">Workflow</h2>
        <p className="mt-1 text-sm text-text-2">{stepCount === 0 ? "No steps yet." : `${stepCount} step${stepCount === 1 ? "" : "s"}. Select one to edit it.`}</p>
      </div>
      <div className="space-y-6 px-6 pb-8">
        <FieldIssues issues={general} />
        <div className="space-y-2">
          <Field label="Name">
            <Input value={name} maxLength={120} autoFocus={autoFocus} onFocus={onCheckpoint} onChange={(e) => onEdit({ name: e.target.value })} aria-invalid={!name.trim()} />
          </Field>
          {!name.trim() && <FieldIssues issues={[{ level: "error", code: "name", message: "Give the workflow a name." }]} />}
        </div>
        <Field label="Description" hint="Optional. Shown in the workflow list.">
          <Textarea value={description} rows={4} maxLength={2000} onFocus={onCheckpoint} onChange={(e) => onEdit({ description: e.target.value })} placeholder="What is this workflow for?" />
        </Field>
      </div>
    </Panel>
  );
}
