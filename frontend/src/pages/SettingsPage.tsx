import { Plus, RefreshCw, Save, Sparkles, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { useDemo, useMeta, useProviders, useRoleMutations, useRoles, useSaveSettings, useSettings } from "@/api/hooks";
import type { AppSettings, ProviderStatus, Role } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { ErrorNote, Notice, Spinner } from "@/components/ui/display";
import { Field, Input, NativeSelect, Segmented, Switch, Textarea } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { roleColor } from "@/lib/status";
import { cn } from "@/lib/utils";
import { useUi } from "@/stores/ui";

const SECTIONS = [
  ["provider", "Provider"],
  ["execution", "Execution"],
  ["permissions", "Permissions"],
  ["roles", "Agent roles"],
  ["scanning", "Scanning"],
  ["graph", "Graph"],
  ["demo", "Demo data"],
] as const;

const PROFILE_LABEL: Record<string, string> = {
  read_only: "Read only",
  edit: "Edit files, ask before shell",
  edit_and_test: "Edit files and run tests",
  trusted_shell: "Edit files and run any command",
};

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-4 space-y-4 border-b border-line-soft pb-8">
      <header>
        <h2 className="text-base font-semibold">{title}</h2>
        {description && <p className="text-xs text-text-2">{description}</p>}
      </header>
      {children}
    </section>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3">
      <span className="pt-0.5">
        <Switch checked={checked} onCheckedChange={onChange} label={label} />
      </span>
      <span className="text-sm">
        {label}
        {hint && <span className="block text-xs text-text-3">{hint}</span>}
      </span>
    </label>
  );
}

function ProviderCard({ status, selected, onSelect }: { status: ProviderStatus; selected: boolean; onSelect: () => void }) {
  const titles: Record<string, string> = { subscription: "Claude subscription", api_key: "Anthropic API key", vertex: "Google Vertex AI" };
  const billing: Record<string, string> = {
    subscription: "Uses your existing Claude Code login and plan limits.",
    api_key: "Billed per token to your Anthropic Console account.",
    vertex: "Billed to your Google Cloud project.",
  };
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn("flex flex-col gap-1.5 rounded-[10px] border p-3 text-left transition-colors", selected ? "border-accent bg-accent-soft" : "border-line hover:border-text-3")}
    >
      <span className="flex items-center gap-2 text-sm font-semibold">
        <span className="size-2 rounded-full" style={{ background: status.ready ? "var(--st-ok)" : "var(--st-fail)" }} />
        {titles[status.provider]}
      </span>
      <span className="text-xs text-text-2">{billing[status.provider]}</span>
      <span className={cn("text-xs", status.ready ? "text-text" : "text-st-fail")}>{status.summary}</span>
    </button>
  );
}

function RoleEditor({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const { create, update, remove } = useRoleMutations();
  const [draft, setDraft] = useState({
    key: role?.key ?? "",
    name: role?.name ?? "",
    description: role?.description ?? "",
    system_prompt: role?.system_prompt ?? "",
    permission_profile: role?.permission_profile ?? "read_only",
    model: role?.model ?? "",
    max_turns: role?.max_turns ? String(role.max_turns) : "",
    extra_allowed_tools: (role?.extra_allowed_tools ?? []).join("\n"),
    enabled: role?.enabled ?? true,
  });
  const body = {
    name: draft.name,
    description: draft.description,
    system_prompt: draft.system_prompt,
    permission_profile: draft.permission_profile as Role["permission_profile"] as never,
    model: draft.model || null,
    max_turns: draft.max_turns ? Number(draft.max_turns) : null,
    extra_allowed_tools: draft.extra_allowed_tools.split("\n").map((t) => t.trim()).filter(Boolean),
    enabled: draft.enabled,
  };
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="lg"
      title={role ? `Edit ${role.name}` : "New agent role"}
      description="Roles shape what an agent focuses on and what it may do. Permissions are enforced by Bosun, not just requested in the prompt."
      footer={
        <>
          {role && !role.builtin && (
            <Button variant="danger" className="mr-auto" onClick={() => remove.mutate(role.id, { onSuccess: onClose })}>
              <Trash2 /> Delete
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!draft.name || (!role && !/^[a-z][a-z0-9_-]{1,31}$/.test(draft.key))}
            onClick={() =>
              role ? update.mutate({ id: role.id, body: body as never }, { onSuccess: onClose }) : create.mutate({ ...body, key: draft.key } as never, { onSuccess: onClose })
            }
          >
            Save role
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="grid grid-cols-2 gap-3">
          {!role && (
            <Field label="Key" hint="Lowercase identifier used in plans, e.g. docs-writer.">
              <Input value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
            </Field>
          )}
          <Field label="Name">
            <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </Field>
          <Field label="Permissions">
            <NativeSelect value={draft.permission_profile} disabled={role?.is_coordinator} onChange={(e) => setDraft({ ...draft, permission_profile: e.target.value })}>
              {Object.entries(PROFILE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Model override" hint="Empty uses the run's model.">
            <Input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} placeholder="claude-sonnet-5-5" />
          </Field>
          <Field label="Max turns">
            <Input type="number" value={draft.max_turns} onChange={(e) => setDraft({ ...draft, max_turns: e.target.value })} placeholder="Run default" />
          </Field>
        </div>
        <Field label="When to use this role" hint="The coordinator reads this when assigning subtasks.">
          <Textarea rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        </Field>
        <Field label="Role instructions">
          <Textarea rows={6} value={draft.system_prompt} onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} />
        </Field>
        <Field label="Additional auto-allowed tool rules" hint='One per line, e.g. Bash(npm run lint:*). Leave empty for the profile defaults.'>
          <Textarea rows={3} value={draft.extra_allowed_tools} onChange={(e) => setDraft({ ...draft, extra_allowed_tools: e.target.value })} className="font-mono text-xs" />
        </Field>
        {!role?.is_coordinator && <Toggle label="Enabled" checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} />}
      </div>
    </Dialog>
  );
}

export function SettingsPage() {
  const { data: saved, isLoading, error } = useSettings();
  const { data: providers = [], refetch, isFetching } = useProviders();
  const { data: roles = [] } = useRoles();
  const { data: meta } = useMeta();
  const save = useSaveSettings();
  const demo = useDemo();
  const graph = useUi((s) => s.graph);
  const setGraph = useUi((s) => s.setGraph);
  const [draft, setDraft] = useState<AppSettings | null>(null);
  const [editingRole, setEditingRole] = useState<Role | null | "new">(null);

  useEffect(() => {
    if (saved && !draft) setDraft(saved);
  }, [saved, draft]);

  if (isLoading || !draft) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  if (error) return <div className="p-6"><ErrorNote title="Settings could not be loaded">{(error as Error).message}</ErrorNote></div>;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const ex = draft.execution;
  const setEx = (patch: Partial<AppSettings["execution"]>) => setDraft({ ...draft, execution: { ...ex, ...patch } });
  const setProvider = (patch: Partial<AppSettings["provider"]>) => setDraft({ ...draft, provider: { ...draft.provider, ...patch } });
  const setScan = (patch: Partial<AppSettings["scan"]>) => setDraft({ ...draft, scan: { ...draft.scan, ...patch } });
  const selectedStatus = providers.find((p) => p.provider === draft.provider.provider);
  const num = (v: string, fallback: number) => (Number.isFinite(Number(v)) && v !== "" ? Number(v) : fallback);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Settings"
        subtitle="Provider, execution limits, permissions and agent roles. Changes apply to new runs."
        actions={
          <Button variant="primary" size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate(draft)}>
            <Save /> {dirty ? "Save changes" : "Saved"}
          </Button>
        }
      />
      <div className="flex min-h-0 flex-1">
        <nav aria-label="Settings sections" className="hidden w-48 shrink-0 flex-col gap-0.5 border-r border-line-soft p-3 md:flex">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={`#${id}`} className="rounded-control px-2 py-1.5 text-sm text-text-2 hover:bg-raised hover:text-text">
              {label}
            </a>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-8 px-6 py-6">
            <Section id="provider" title="Provider" description="Which Claude account runs agents. Bosun never switches providers on its own, and stops any session that authenticates differently.">
              <div className="grid gap-3 md:grid-cols-3">
                {providers.map((p) => (
                  <ProviderCard key={p.provider} status={p} selected={draft.provider.provider === p.provider} onSelect={() => setProvider({ provider: p.provider as never })} />
                ))}
                {providers.length === 0 && <Spinner />}
              </div>
              <div className="flex items-center gap-2">
                <Button size="xs" variant="ghost" onClick={() => void refetch()} disabled={isFetching}>
                  <RefreshCw className={cn(isFetching && "animate-spin")} /> Re-check
                </Button>
                {selectedStatus?.details?.subscription_type ? (
                  <span className="text-xs text-text-3">
                    {String(selectedStatus.details.email ?? "")} {selectedStatus.details.subscription_type ? `(${String(selectedStatus.details.subscription_type)} plan)` : ""}
                  </span>
                ) : null}
              </div>
              {draft.provider.provider === "subscription" && <Notice>{providers[0]?.policy_note}</Notice>}
              {draft.provider.provider === "api_key" && <Notice tone="warn">API usage is billed per token. Set BOSUN_ANTHROPIC_API_KEY in Bosun's .env file.</Notice>}
              {draft.provider.provider === "vertex" && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="GCP project">
                    <Input value={draft.provider.vertex_project_id ?? ""} onChange={(e) => setProvider({ vertex_project_id: e.target.value || null })} />
                  </Field>
                  <Field label="Region">
                    <Input value={draft.provider.vertex_region ?? ""} onChange={(e) => setProvider({ vertex_region: e.target.value || null })} placeholder="global" />
                  </Field>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field label="Default model" hint="Empty uses Claude Code's default for the provider.">
                  <NativeSelect value={draft.provider.model ?? ""} onChange={(e) => setProvider({ model: e.target.value || null })}>
                    <option value="">Provider default</option>
                    <option value="claude-opus-5-5">claude-opus-5-5</option>
                    <option value="claude-sonnet-5-5">claude-sonnet-5-5</option>
                    <option value="claude-haiku-5-5">claude-haiku-5-5</option>
                    <option value="claude-fable-5-1">claude-fable-5-1</option>
                  </NativeSelect>
                </Field>
                <Field label="Effort">
                  <NativeSelect value={draft.provider.effort ?? ""} onChange={(e) => setProvider({ effort: (e.target.value || null) as never })}>
                    <option value="">Model default</option>
                    {["low", "medium", "high", "xhigh", "max"].map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              </div>
            </Section>

            <Section id="execution" title="Execution" description="Defaults for new runs and the limits Bosun enforces.">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <Field label="Default backend">
                  <Segmented value={ex.backend} onChange={(backend) => setEx({ backend })} options={[{ value: "claude", label: "Claude Code" }, { value: "demo", label: "Demo" }]} />
                </Field>
                <Field label="Default strategy">
                  <NativeSelect value={ex.default_mode} onChange={(e) => setEx({ default_mode: e.target.value as never })}>
                    <option value="single">Single agent</option>
                    <option value="team">Agent team</option>
                    <option value="auto">Automatic</option>
                  </NativeSelect>
                </Field>
                <Field label="Agents running at once" hint="Across all runs.">
                  <Input type="number" min={1} max={12} value={ex.max_concurrent_sessions} onChange={(e) => setEx({ max_concurrent_sessions: num(e.target.value, 3) })} />
                </Field>
                <Field label="Parallel agents per team">
                  <Input type="number" min={1} max={6} value={ex.max_parallel_per_run} onChange={(e) => setEx({ max_parallel_per_run: num(e.target.value, 2) })} />
                </Field>
                <Field label="Largest team">
                  <Input type="number" min={1} max={8} value={ex.max_team_size} onChange={(e) => setEx({ max_team_size: num(e.target.value, 4) })} />
                </Field>
                <Field label="Max turns per agent">
                  <Input type="number" min={1} value={ex.default_max_turns} onChange={(e) => setEx({ default_max_turns: num(e.target.value, 60) })} />
                </Field>
                <Field label="Session time limit (min)">
                  <Input type="number" min={1} value={ex.session_timeout_minutes} onChange={(e) => setEx({ session_timeout_minutes: num(e.target.value, 45) })} />
                </Field>
                <Field label="Approval timeout (min)" hint="Unanswered requests are denied.">
                  <Input type="number" min={1} value={ex.approval_timeout_minutes} onChange={(e) => setEx({ approval_timeout_minutes: num(e.target.value, 30) })} />
                </Field>
                <Field label="Attempts per subtask">
                  <Input type="number" min={1} max={5} value={ex.subtask_max_attempts} onChange={(e) => setEx({ subtask_max_attempts: num(e.target.value, 2) })} />
                </Field>
              </div>
              <div className="space-y-3">
                <Toggle label="Approve team plans before agents start" checked={ex.require_plan_approval} onChange={(require_plan_approval) => setEx({ require_plan_approval })} />
                <Toggle label="Allow Claude subagents in single-agent runs" hint="The agent may delegate to role subagents through the Agent tool; delegation appears in the graph." checked={ex.allow_sdk_subagents} onChange={(allow_sdk_subagents) => setEx({ allow_sdk_subagents })} />
                <Toggle label="Allow web search and fetch" hint="Off by default: agents cannot reach the network through Claude's web tools." checked={ex.allow_web_tools} onChange={(allow_web_tools) => setEx({ allow_web_tools })} />
                <Toggle label="Commit agent changes on the run branch" hint="Keeps diffs and merges clean. Nothing is ever pushed." checked={ex.commit_agent_changes} onChange={(commit_agent_changes) => setEx({ commit_agent_changes })} />
                <Toggle
                  label="Run shell commands in Claude Code's sandbox"
                  hint="Adds an operating-system boundary (bubblewrap on Linux, Seatbelt on macOS) around agents' shell commands. Commands still need your approval. Commands that need the network or write outside the worktree will fail."
                  checked={ex.sandbox_bash}
                  onChange={(sandbox_bash) => setEx({ sandbox_bash })}
                />
                {ex.sandbox_bash && meta && !meta.sandbox.supported && (
                  <Notice tone="warn" className="ml-12">
                    {meta.sandbox.missing.length
                      ? `This machine is missing ${meta.sandbox.missing.join(" and ")}. Runs will stop before doing anything until ${meta.sandbox.missing.length > 1 ? "they are" : "it is"} installed.`
                      : `Claude Code's sandbox does not support ${meta.sandbox.platform}. Runs will stop before doing anything while this is on.`}
                  </Notice>
                )}
              </div>
            </Section>

            <Section id="permissions" title="Permissions" description="Shell commands that run without asking. Everything else that needs permission comes to you as an approval.">
              <Field label="Safe commands for every editing role" hint="Claude Code permission rules, one per line, e.g. Bash(git diff:*).">
                <Textarea rows={6} className="font-mono text-xs" value={ex.auto_allow_bash.join("\n")} onChange={(e) => setEx({ auto_allow_bash: e.target.value.split("\n").map((l) => l.trim()).filter(Boolean) })} />
              </Field>
              <Field label="Test commands for roles that may run tests">
                <Textarea rows={6} className="font-mono text-xs" value={ex.test_commands.join("\n")} onChange={(e) => setEx({ test_commands: e.target.value.split("\n").map((l) => l.trim()).filter(Boolean) })} />
              </Field>
              <Notice>File tools are always confined to the run's worktree, and bypass permission modes are never used.</Notice>
            </Section>

            <Section id="roles" title="Agent roles" description="The coordinator assigns subtasks to these roles. Single-agent runs use one of them.">
              <ul className="divide-y divide-line-soft rounded-[10px] border border-line-soft">
                {roles.map((r) => (
                  <li key={r.id} className="flex items-center gap-3 px-4 py-2.5">
                    <span className="h-6 w-[3px] rounded-full" style={{ background: roleColor(r.key, r.color) }} />
                    <div className="min-w-0 flex-1">
                      <p className={cn("text-sm font-medium", !r.enabled && "text-text-3 line-through")}>
                        {r.name}
                        <span className="ml-2 text-xs font-normal text-text-3">{PROFILE_LABEL[r.permission_profile]}</span>
                      </p>
                      <p className="truncate text-xs text-text-2">{r.description}</p>
                    </div>
                    {!r.is_coordinator && (
                      <Switch checked={r.enabled} onCheckedChange={() => setEditingRole(r)} label={`${r.name} enabled`} />
                    )}
                    <Button size="sm" variant="ghost" onClick={() => setEditingRole(r)}>
                      Edit
                    </Button>
                  </li>
                ))}
              </ul>
              <Button size="sm" variant="secondary" onClick={() => setEditingRole("new")}>
                <Plus /> Add role
              </Button>
            </Section>

            <Section id="scanning" title="Scanning" description="Defaults for TODO discovery. Workspaces can override them.">
              <Field label="Annotation tags">
                <Input value={draft.scan.tags.join(", ")} onChange={(e) => setScan({ tags: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })} />
              </Field>
              <Field label="Excluded paths" hint="gitignore-style patterns, applied in addition to .gitignore.">
                <Textarea rows={8} className="font-mono text-xs" value={draft.scan.exclude_globs.join("\n")} onChange={(e) => setScan({ exclude_globs: e.target.value.split("\n").map((t) => t.trim()).filter(Boolean) })} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Max file size (KB)">
                  <Input type="number" value={draft.scan.max_file_kb} onChange={(e) => setScan({ max_file_kb: num(e.target.value, 512) })} />
                </Field>
                <Field label="Max files per scan">
                  <Input type="number" value={draft.scan.max_files} onChange={(e) => setScan({ max_files: num(e.target.value, 50000) })} />
                </Field>
              </div>
            </Section>

            <Section id="graph" title="Graph" description="Stored in this browser.">
              <div className="flex flex-wrap items-center gap-6">
                <Field label="Layout">
                  <Segmented value={graph.direction} onChange={(direction) => setGraph({ direction })} options={[{ value: "LR", label: "Left to right" }, { value: "TB", label: "Top down" }]} />
                </Field>
                <Toggle label="Group repeated tool calls" checked={graph.aggregate} onChange={(aggregate) => setGraph({ aggregate })} />
                <Toggle label="Minimap" checked={graph.minimap} onChange={(minimap) => setGraph({ minimap })} />
              </div>
            </Section>

            <Section id="demo" title="Demo data" description="A small project with real TODOs whose agents run on the demo backend. No Claude calls are made.">
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => demo.seed.mutate()} disabled={demo.seed.isPending}>
                  <Sparkles /> Load demo
                </Button>
                <Button variant="ghost" onClick={() => demo.remove.mutate()} disabled={demo.remove.isPending}>
                  <Trash2 /> Remove demo data
                </Button>
              </div>
            </Section>
          </div>
        </div>
      </div>
      {editingRole && <RoleEditor role={editingRole === "new" ? null : editingRole} onClose={() => setEditingRole(null)} />}
    </div>
  );
}
