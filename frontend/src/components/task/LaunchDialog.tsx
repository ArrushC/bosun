import { Bot, Check, GitBranch, Lock, Network, ShieldCheck, Sparkles, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { usePreviewRun, useRoles, useSettings, useStartRun } from "@/api/hooks";
import type { ExecutionMode, RunCreate, RunPreview, TaskDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { ErrorNote, Notice, RoleTag, Spinner } from "@/components/ui/display";
import { Checkbox, Field, Input, NativeSelect, Segmented, Switch, Textarea } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { roleColor } from "@/lib/status";
import { cn, newIdempotencyKey } from "@/lib/utils";

const MODEL_OPTIONS = ["", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"];

function ScopeTable({ scope }: { scope: Record<string, { name: string; profile: string; description: string; tools: string[]; auto_allowed: string[]; model?: string | null }> }) {
  return (
    <div className="divide-y divide-line-soft overflow-hidden rounded-[14px] bg-sunken">
      {Object.entries(scope).map(([key, role]) => {
        const autoTools = role.auto_allowed.filter((t) => !t.startsWith("Bash("));
        const autoBash = role.auto_allowed.filter((t) => t.startsWith("Bash(")).map((t) => t.slice(5, -1).replace(/:\*$/, " …"));
        const gated = role.tools.filter((t) => !role.auto_allowed.includes(t));
        return (
          <div key={key} className="grid gap-2 px-4 py-3.5 sm:grid-cols-[160px_1fr]">
            <div>
              <RoleTag name={role.name} color={roleColor(key)} />
              {role.model && <p className="mono mt-1 text-xs text-text-3">{role.model}</p>}
            </div>
            <div className="space-y-1.5 text-sm">
              <p className="text-text-2">{role.description}</p>
              <p>
                <span className="text-text-3">Runs without asking: </span>
                <span className="mono">{autoTools.join(", ")}</span>
                {autoBash.length > 0 && <span className="text-text-3"> and shell commands </span>}
                {autoBash.length > 0 && <span className="mono">{autoBash.join(", ")}</span>}
              </p>
              {gated.length > 0 && (
                <p>
                  <span className="text-text-3">Asks you first: </span>
                  <span className="mono">{gated.map((t) => (t === "Bash" ? "other shell commands" : t)).join(", ")}</span>
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function LaunchDialog({ task, open, onOpenChange }: { task: TaskDetail; open: boolean; onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate();
  const { data: roles = [] } = useRoles();
  const { data: settings } = useSettings();
  const preview = usePreviewRun(task.id);
  const start = useStartRun(task.id);
  const idempotencyKey = useRef(newIdempotencyKey());
  const workers = roles.filter((r) => r.enabled && !r.is_coordinator);

  const [mode, setMode] = useState<ExecutionMode>((task.execution_mode as ExecutionMode) ?? "single");
  const [role, setRole] = useState("implementer");
  const [teamRoles, setTeamRoles] = useState<string[]>([]);
  const [backend, setBackend] = useState<"claude" | "demo">("claude");
  const [model, setModel] = useState("");
  const [planApproval, setPlanApproval] = useState(true);
  const [subagents, setSubagents] = useState(false);
  const [maxTurns, setMaxTurns] = useState("");
  const [instructions, setInstructions] = useState("");
  const [result, setResult] = useState<RunPreview | null>(null);

  useEffect(() => {
    if (!open) return;
    idempotencyKey.current = newIdempotencyKey();
    setMode((task.execution_mode as ExecutionMode) ?? settings?.execution.default_mode ?? "single");
    setBackend(task.is_demo ? "demo" : ((settings?.execution.backend as "claude" | "demo") ?? "claude"));
    setPlanApproval(settings?.execution.require_plan_approval ?? true);
    setSubagents(settings?.execution.allow_sdk_subagents ?? false);
    setInstructions("");
    setResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, task.id]);

  useEffect(() => {
    if (workers.length && !teamRoles.length) setTeamRoles(workers.map((w) => w.key));
    if (workers.length && !workers.some((w) => w.key === role)) setRole(workers[0].key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workers.length]);

  const body: RunCreate = useMemo(
    () => ({
      mode,
      role: mode === "single" ? role : undefined,
      team_roles: mode === "single" ? undefined : teamRoles,
      backend,
      model: model || undefined,
      require_plan_approval: mode === "single" ? undefined : planApproval,
      allow_subagents: mode === "single" ? subagents : undefined,
      max_turns: maxTurns ? Number(maxTurns) : undefined,
      instructions: instructions.trim() || undefined,
    }),
    [mode, role, teamRoles, backend, model, planApproval, subagents, maxTurns, instructions],
  );

  const bodyKey = JSON.stringify(body);
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => preview.mutate(body, { onSuccess: setResult }), 220);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, bodyKey]);

  // Synchronous guard: a fast double click must never send two launches
  // (the idempotency key also makes the server collapse duplicates).
  const launching = useRef(false);
  const launch = async () => {
    if (launching.current) return;
    launching.current = true;
    try {
      const run = await start.mutateAsync({ body, idempotencyKey: idempotencyKey.current });
      onOpenChange(false);
      navigate(`/runs/${run.id}`);
    } finally {
      launching.current = false;
    }
  };

  const scope = (result?.scope ?? {}) as Parameters<typeof ScopeTable>[0]["scope"];
  const workspace = result?.workspace as { name?: string; path?: string; isolation?: string; base_branch?: string } | undefined;
  const provider = result?.provider as { provider?: string; ready?: boolean; summary?: string; backend?: string } | undefined;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={`Run ${task.key} with agents`}
      description="Review what will run and what agents are allowed to do. Nothing starts until you confirm."
      footer={
        <>
          {preview.isPending && <Spinner className="mr-auto" />}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!result?.can_start || start.isPending} onClick={() => void launch().catch(() => {})}>
            <Sparkles /> {start.isPending ? "Starting…" : backend === "demo" ? "Start demo run" : "Start run"}
          </Button>
        </>
      }
    >
      <div className="grid gap-7">
        <Field label="Strategy">
          <div className="grid gap-3 sm:grid-cols-3">
            {(
              [
                { value: "single", icon: Bot, title: "Single agent", text: "One agent does the whole task. Best for focused changes." },
                { value: "team", icon: Users, title: "Agent team", text: "A coordinator plans subtasks and delegates to role agents." },
                { value: "auto", icon: Network, title: "Automatic", text: "The coordinator picks one agent or a team, within your limits." },
              ] as const
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setMode(opt.value)}
                aria-pressed={mode === opt.value}
                className={cn(
                  "flex flex-col gap-1 rounded-[8px] border px-3 py-2.5 text-left transition-colors",
                  mode === opt.value ? "border-accent bg-accent-soft" : "border-line hover:border-text-3",
                )}
              >
                <span className="flex items-center gap-2 text-sm font-semibold">
                  <opt.icon className="size-4 text-text-2" /> {opt.title}
                </span>
                <span className="text-sm text-text-2">{opt.text}</span>
              </button>
            ))}
          </div>
        </Field>

        {mode === "single" ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Agent role">
              <NativeSelect value={role} onChange={(e) => setRole(e.target.value)}>
                {workers.map((w) => (
                  <option key={w.key} value={w.key}>
                    {w.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Claude subagents" hint="Let the agent delegate to role subagents through the Agent tool.">
              <div className="flex h-10 items-center">
                <Switch checked={subagents} onCheckedChange={setSubagents} label="Allow subagents" />
              </div>
            </Field>
          </div>
        ) : (
          <div className="grid gap-3">
            <Field label="Roles the coordinator may use">
              <div className="flex flex-wrap gap-2">
                {workers.map((w) => {
                  const on = teamRoles.includes(w.key);
                  return (
                    <button
                      key={w.key}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setTeamRoles(on ? teamRoles.filter((r) => r !== w.key) : [...teamRoles, w.key])}
                      className={cn(
                        "inline-flex h-7 items-center gap-1.5 rounded-control border px-2 text-sm",
                        on ? "border-text-3 bg-raised text-text" : "border-line text-text-3 line-through",
                      )}
                    >
                      <span className="size-2 rounded-full" style={{ background: roleColor(w.key, w.color) }} />
                      {w.name}
                      {on && <Check className="size-3 text-text-2" />}
                    </button>
                  );
                })}
              </div>
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={planApproval} onCheckedChange={setPlanApproval} aria-label="Require plan approval" />
              Show me the coordinator's plan before any agent starts
            </label>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Backend">
            <Segmented
              value={backend}
              onChange={setBackend}
              options={[
                { value: "claude", label: "Claude Code" },
                { value: "demo", label: "Demo", title: "Deterministic demo agents; no Claude calls" },
              ]}
            />
          </Field>
          <Field label="Model" hint="Empty uses the provider default.">
            <NativeSelect value={model} onChange={(e) => setModel(e.target.value)} disabled={backend === "demo"}>
              {MODEL_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {m || `Default${settings?.provider.model ? ` (${settings.provider.model})` : ""}`}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Max turns per agent">
            <Input
              type="number"
              min={1}
              max={500}
              placeholder={String(settings?.execution.default_max_turns ?? 60)}
              value={maxTurns}
              onChange={(e) => setMaxTurns(e.target.value)}
            />
          </Field>
        </div>

        <Field label="Extra instructions for this run (optional)">
          <Textarea rows={3} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="e.g. Keep the public API unchanged." />
        </Field>

        <section className="grid gap-3">
          <h3 className="flex items-center gap-2 text-base font-semibold">
            <ShieldCheck className="size-[18px] text-text-2" /> Plan and scope
          </h3>
          {result?.errors.map((e) => (
            <ErrorNote key={e} title={e} />
          ))}
          {result?.warnings.map((w) => (
            <Notice key={w} tone="warn">
              {w}
            </Notice>
          ))}
          {workspace?.path && (
            <div className="grid gap-2.5 rounded-[14px] bg-sunken px-5 py-4 text-sm">
              <p className="flex items-center gap-2">
                <GitBranch className="size-4 text-text-3" />
                {workspace.isolation === "worktree" ? (
                  <span>
                    Isolated git worktree from <span className="mono">{workspace.base_branch ?? "HEAD"}</span>; your checkout is not touched.
                  </span>
                ) : (
                  <span>Edits files in place (not a git repository).</span>
                )}
              </p>
              <p className="flex items-center gap-2 text-text-2">
                <Lock className="size-4 text-text-3" />
                <span>
                  File tools are limited to {workspace.isolation === "worktree" ? "the run's worktree" : "this folder"}; shell commands that reach into your home directory or other projects are blocked
                  {settings?.execution.sandbox_bash ? " and run inside Claude Code's OS sandbox" : ""}. Nothing is pushed, opened as a pull request or deployed.
                </span>
              </p>
              {provider && (
                <p className="flex items-center gap-2 text-text-2">
                  <span className="size-2 rounded-full" style={{ background: provider.ready ? "var(--st-ok)" : "var(--st-fail)" }} />
                  {provider.summary}
                </p>
              )}
            </div>
          )}
          {Object.keys(scope).length > 0 && <ScopeTable scope={scope} />}
        </section>
      </div>
    </Dialog>
  );
}
