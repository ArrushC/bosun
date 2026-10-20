import { CircleDot, GitPullRequest, Lock, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useConnectionMutations, useConnections, useGitHubRepos, useGitHubStatus, useWorkspaces } from "@/api/hooks";
import type { Connection } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, GithubMark, Spinner } from "@/components/ui/display";
import { Checkbox, Field, Input, NativeSelect, SearchInput, Segmented } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { cn, relativeTime } from "@/lib/utils";

interface Filters {
  state: "open" | "all";
  labels: string[];
  milestone: string | null;
  author: string | null;
  assignee: string | null;
  include_issues: boolean;
  include_prs: boolean;
}

const DEFAULT_FILTERS: Filters = { state: "open", labels: [], milestone: null, author: null, assignee: null, include_issues: true, include_prs: true };

function ConnectionDialog({ connection, onClose }: { connection?: Connection; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  const repos = useGitHubRepos(debounced, true);
  const { data: workspaces = [] } = useWorkspaces();
  const { create, update } = useConnectionMutations();
  const config = connection?.config as { repos?: string[]; filters?: Filters; sync_interval_minutes?: number } | undefined;
  const [selected, setSelected] = useState<string[]>(config?.repos ?? []);
  const [manualRepo, setManualRepo] = useState("");
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS, ...(config?.filters ?? {}) });
  const [labels, setLabels] = useState((config?.filters?.labels ?? []).join(", "));
  const [interval, setInterval] = useState(String(config?.sync_interval_minutes ?? 0));
  const [workspaceId, setWorkspaceId] = useState(connection?.workspace_id ?? workspaces[0]?.id ?? "");

  const body = {
    repos: selected,
    filters: { ...filters, labels: labels.split(",").map((l) => l.trim()).filter(Boolean), milestone: filters.milestone || null, author: filters.author || null, assignee: filters.assignee || null },
    sync_interval_minutes: Number(interval),
    workspace_id: workspaceId || null,
  };
  const busy = create.isPending || update.isPending;
  const toggle = (name: string) => setSelected((s) => (s.includes(name) ? s.filter((r) => r !== name) : [...s, name]));

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="lg"
      title={connection ? `Edit ${connection.name}` : "Connect GitHub repositories"}
      description="Issues and pull requests are imported read-only into the Inbox. Nothing is written back to GitHub."
      footer={
        <>
          <span className="mr-auto text-sm text-text-3">{selected.length} repositor{selected.length === 1 ? "y" : "ies"} selected</span>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!selected.length || busy}
            onClick={() => (connection ? update.mutate({ id: connection.id, body }, { onSuccess: onClose }) : create.mutate({ ...body, sync_now: true }, { onSuccess: onClose }))}
          >
            {busy ? "Syncing…" : connection ? "Save" : "Connect and sync"}
          </Button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-[1fr_280px]">
        <div className="space-y-2">
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your repositories" aria-label="Search repositories" />
          <div className="h-80 overflow-y-auto rounded-[12px] border border-line-soft">
            {repos.isLoading && <div className="flex justify-center p-6"><Spinner /></div>}
            {repos.error && <div className="p-3"><ErrorNote title="Repositories could not be listed">{(repos.error as Error).message}</ErrorNote></div>}
            <ul className="p-1.5">
              {repos.data?.map((r) => (
                <li key={r.full_name}>
                  <label className={cn("flex cursor-pointer items-center gap-3 rounded-[8px] px-3 py-2 hover:bg-raised", selected.includes(r.full_name) && "bg-accent-soft")}>
                    <Checkbox checked={selected.includes(r.full_name)} onCheckedChange={() => toggle(r.full_name)} aria-label={r.full_name} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-sm">
                        {r.full_name} {r.private && <Lock className="size-3 text-text-3" />}
                      </span>
                      {r.description && <span className="block truncate text-xs text-text-3">{r.description}</span>}
                    </span>
                    <span className="shrink-0 text-xs text-text-3">{r.open_issues_count} open</span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex gap-2">
            <Input value={manualRepo} onChange={(e) => setManualRepo(e.target.value)} placeholder="owner/name (for repos not listed)" aria-label="Add repository by name" />
            <Button
              variant="outline"
              onClick={() => {
                if (/^[\w.-]+\/[\w.-]+$/.test(manualRepo.trim())) {
                  setSelected((s) => [...new Set([...s, manualRepo.trim()])]);
                  setManualRepo("");
                }
              }}
            >
              Add
            </Button>
          </div>
          {selected.length > 0 && <p className="text-sm text-text-2">Selected: {selected.join(", ")}</p>}
        </div>
        <div className="space-y-3">
          <Field label="Import">
            <div className="flex flex-col gap-1.5 text-sm">
              <label className="flex items-center gap-2">
                <Checkbox checked={filters.include_issues} onCheckedChange={(v) => setFilters({ ...filters, include_issues: v })} aria-label="Issues" />
                <CircleDot className="size-3.5 text-text-3" /> Issues
              </label>
              <label className="flex items-center gap-2">
                <Checkbox checked={filters.include_prs} onCheckedChange={(v) => setFilters({ ...filters, include_prs: v })} aria-label="Pull requests" />
                <GitPullRequest className="size-3.5 text-text-3" /> Pull requests
              </label>
            </div>
          </Field>
          <Field label="State">
            <Segmented value={filters.state} onChange={(state) => setFilters({ ...filters, state })} options={[{ value: "open", label: "Open only" }, { value: "all", label: "Open and closed" }]} />
          </Field>
          <Field label="Labels (all must match)">
            <Input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="bug, good first issue" />
          </Field>
          <Field label="Milestone">
            <Input value={filters.milestone ?? ""} onChange={(e) => setFilters({ ...filters, milestone: e.target.value })} placeholder="v1.0, * or none" />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Author">
              <Input value={filters.author ?? ""} onChange={(e) => setFilters({ ...filters, author: e.target.value })} placeholder="login" />
            </Field>
            <Field label="Assignee">
              <Input value={filters.assignee ?? ""} onChange={(e) => setFilters({ ...filters, assignee: e.target.value })} placeholder="login, * or none" />
            </Field>
          </div>
          <Field label="Sync">
            <NativeSelect value={interval} onChange={(e) => setInterval(e.target.value)}>
              <option value="0">Manually</option>
              <option value="15">Every 15 minutes</option>
              <option value="60">Every hour</option>
              <option value="360">Every 6 hours</option>
              <option value="1440">Daily</option>
            </NativeSelect>
          </Field>
          <Field label="Local checkout for agents" hint="Tasks imported from these repos run in this workspace.">
            <NativeSelect value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)}>
              <option value="">Choose when importing</option>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

const STATUS_TEXT: Record<string, { label: string; color: string }> = {
  idle: { label: "Not synced yet", color: "var(--st-idle)" },
  syncing: { label: "Syncing", color: "var(--st-run)" },
  ok: { label: "In sync", color: "var(--st-ok)" },
  error: { label: "Sync failed", color: "var(--st-fail)" },
  rate_limited: { label: "Rate limited", color: "var(--st-approval)" },
};

export function GitHubSection() {
  const { data: status, isLoading } = useGitHubStatus();
  const { data: connections = [] } = useConnections();
  const { sync, remove } = useConnectionMutations();
  const [dialog, setDialog] = useState<Connection | "new" | null>(null);

  return (
    <section className="space-y-5">
      <header className="flex items-center gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-md font-semibold">
            <GithubMark className="size-5" /> GitHub
          </h2>
          <p className="mt-1 text-sm text-text-2">Bring issues and pull requests into the Inbox.</p>
        </div>
        <Button variant="primary" className="ml-auto" disabled={!status?.connected} onClick={() => setDialog("new")}>
          Connect repositories
        </Button>
      </header>

      <div className="rounded-panel bg-panel px-5 py-4">
        {isLoading ? (
          <Spinner />
        ) : status?.connected ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {status.avatar_url && <img src={status.avatar_url} alt="" className="size-9 rounded-full" />}
            <div>
              <p>
                Connected as <b>@{status.login}</b> through {status.source === "gh" ? "the GitHub CLI" : "BOSUN_GITHUB_TOKEN"}
              </p>
              <p className="mt-0.5 text-sm text-text-3">
                {status.rate_limit?.remaining != null && `${status.rate_limit.remaining} of ${status.rate_limit.limit} API requests left this hour. `}
                Bosun only reads from GitHub.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-1 text-sm">
            <p className="font-medium">GitHub is not connected</p>
            <p className="text-text-2">{status?.message ?? "Install the GitHub CLI and run gh auth login, or set BOSUN_GITHUB_TOKEN."}</p>
          </div>
        )}
      </div>

      {connections.length === 0 ? (
        status?.connected && (
          <EmptyState icon={GithubMark} title="No repositories connected" className="py-6">
            Choose repositories to bring their issues and pull requests into the Inbox.
          </EmptyState>
        )
      ) : (
        <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
          {connections.map((c) => {
            const config = c.config as { repos?: string[]; filters?: Filters; sync_interval_minutes?: number };
            const st = STATUS_TEXT[c.status] ?? STATUS_TEXT.idle;
            return (
              <li key={c.id} className="grid gap-4 px-5 py-4 md:grid-cols-[1fr_auto]">
                <div className="min-w-0">
                  <p className="flex items-center gap-2.5 text-base font-medium">
                    <span className="size-2 rounded-full" style={{ background: st.color }} />
                    {c.name}
                    <span className="text-sm font-normal text-text-3">{st.label}</span>
                  </p>
                  <p className="mt-1.5 text-sm text-text-2">
                    {(config.repos ?? []).join(", ")}. {c.open_items} open items.{" "}
                    {config.filters?.labels?.length ? `Labels: ${config.filters.labels.join(", ")}. ` : ""}
                    {config.sync_interval_minutes ? `Syncs every ${config.sync_interval_minutes} min. ` : "Manual sync. "}
                    {c.last_synced_at && `Last synced ${relativeTime(c.last_synced_at)}.`}
                  </p>
                  {c.last_error && <p className="mt-1.5 text-sm text-st-fail">{c.last_error}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="secondary" disabled={sync.isPending || c.status === "syncing"} onClick={() => sync.mutate(c.id)}>
                    <RefreshCw className={cn(c.status === "syncing" && "animate-spin")} /> Sync now
                  </Button>
                  <Button size="icon-sm" variant="ghost" aria-label="Edit connection" onClick={() => setDialog(c)}>
                    <Pencil />
                  </Button>
                  <Button size="icon-sm" variant="ghost" aria-label="Remove connection" onClick={() => confirm(`Disconnect ${c.name}? Imported tasks stay.`) && remove.mutate(c.id)}>
                    <Trash2 />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-sm leading-6 text-text-3">
        Bosun only reads from GitHub. It never comments, pushes branches or opens pull requests; agents work on local branches and you decide what to push.
      </p>
      {dialog && <ConnectionDialog connection={dialog === "new" ? undefined : dialog} onClose={() => setDialog(null)} />}
    </section>
  );
}
