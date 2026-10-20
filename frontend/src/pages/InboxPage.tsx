import { ChevronDown, ChevronRight, EyeOff, FolderGit2, Inbox, ListPlus, RefreshCw, Undo2 } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useConnectionMutations, useConnections, useDismissRecords, useImportRecords, useRecords, useWorkspaceMutations, useWorkspaces } from "@/api/hooks";
import type { Connection, SourceRecord, TaskStatus, Workspace } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, GithubMark, Md, SOURCE_ICON, SourceIcon, Spinner, TabPanel, Tabs } from "@/components/ui/display";
import { Checkbox, NativeSelect, SearchInput, Segmented } from "@/components/ui/inputs";
import { cn, plural, relativeTime, tildePath } from "@/lib/utils";

type Triage = "untriaged" | "imported" | "dismissed";

/** A workspace with the GitHub connections linked to it, or a connection linked to none. */
interface Project {
  id: string;
  name: string;
  workspace?: Workspace;
  connections: Connection[];
}

function projectsOf(workspaces: Workspace[], connections: Connection[]): Project[] {
  const projects: Project[] = workspaces.map((w) => ({ id: w.id, name: w.name, workspace: w, connections: connections.filter((c) => c.workspace_id === w.id) }));
  for (const c of connections) {
    if (!workspaces.some((w) => w.id === c.workspace_id)) projects.push({ id: c.id, name: c.name, connections: [c] });
  }
  return projects;
}

/** How records were found: by scanning a workspace's code, or from a GitHub connection. */
interface Source {
  key: string;
  icon: ReactNode;
  label: string;
  detail: string;
  items: SourceRecord[];
}

function sourcesOf(records: SourceRecord[], workspaces: Workspace[], connections: Connection[]): Source[] {
  const sources = new Map<string, Source>();
  for (const r of records) {
    const key = r.workspace_id ? `ws:${r.workspace_id}` : `conn:${r.connection_id}`;
    let source = sources.get(key);
    if (!source) {
      const workspace = workspaces.find((w) => w.id === r.workspace_id);
      const connection = connections.find((c) => c.id === r.connection_id);
      source = r.workspace_id
        ? { key, icon: <FolderGit2 className="size-4" />, label: "Code", detail: workspace ? tildePath(workspace.path) : "", items: [] }
        : { key, icon: <GithubMark className="size-4" />, label: "GitHub", detail: connection?.name ?? r.repo ?? "", items: [] };
      sources.set(key, source);
    }
    source.items.push(r);
  }
  // GitHub issues and pull requests first, then what the code scan found.
  return [...sources.values()].sort((a, b) => Number(a.key.startsWith("ws:")) - Number(b.key.startsWith("ws:")));
}

function location(r: SourceRecord) {
  if (r.repo) return `${r.repo}#${r.number}`;
  return `${r.path}:${r.line}`;
}

function RecordRow({ record, checked, onCheck, origin }: { record: SourceRecord; checked: boolean; onCheck: (v: boolean) => void; origin: string }) {
  const [open, setOpen] = useState(false);
  const meta = record.meta as Record<string, unknown>;
  return (
    <li className={cn("transition-colors", checked && "bg-accent-soft")}>
      <div className="flex items-center gap-4 px-5 py-4">
        <Checkbox checked={checked} onCheckedChange={onCheck} aria-label={`Select ${record.title}`} />
        <button onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-3 text-left" aria-expanded={open}>
          <SourceIcon kind={record.kind} className="size-4" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-base">{record.title}</span>
            <span className="mono mt-0.5 block truncate text-xs text-text-3" title={location(record)}>
              {location(record)}
            </span>
          </span>
          {record.tag && <span className="mono shrink-0 rounded-full bg-raised px-2 text-xs text-text-2">{record.tag}</span>}
          <span className="w-24 shrink-0 text-right text-xs text-text-3">{relativeTime(record.external_updated_at ?? record.first_seen_at)}</span>
          {open ? <ChevronDown className="size-4 shrink-0 text-text-3" /> : <ChevronRight className="size-4 shrink-0 text-text-3" />}
        </button>
        {record.task_ids.length > 0 && (
          <Link to={`/board?task=${record.task_ids[0]}`} className="shrink-0 text-sm text-accent-strong hover:underline">
            View task
          </Link>
        )}
      </div>
      {open && (
        // Rows span the page; the details keep a readable line length.
        <div className="max-w-[calc(48rem+60px)] space-y-3 pr-5 pb-5 pl-[60px]">
          <p className="text-sm text-text-2">
            {SOURCE_ICON[record.kind]?.label} in {origin}
            {meta.section ? `, section "${String(meta.section)}"` : ""}
            {meta.owner ? `, owner ${String(meta.owner)}` : ""}
            {record.author ? `, opened by @${record.author}` : ""}
            {meta.head ? `, branch ${String(meta.head)} into ${String(meta.base)}` : ""}
            {record.labels.length > 0 ? `. Labels: ${record.labels.join(", ")}` : ""}
          </p>
          {record.context && (
            <pre className="overflow-x-auto rounded-[10px] bg-sunken px-4 py-3 font-mono text-xs leading-5 text-text-2">{record.context}</pre>
          )}
          {record.body && record.kind.startsWith("github") && (
            <div className="max-h-72 overflow-y-auto rounded-[10px] bg-sunken px-4 py-3">
              <Md className="text-sm">{record.body}</Md>
            </div>
          )}
          {record.url && (
            <a href={record.url} target="_blank" rel="noreferrer noopener" className="text-sm text-accent-strong hover:underline">
              Open on GitHub
            </a>
          )}
        </div>
      )}
    </li>
  );
}

function SourceSection({ source, selected, onSelect, origin }: {
  source: Source;
  selected: Set<string>;
  onSelect: (ids: string[], on: boolean) => void;
  origin: (r: SourceRecord) => string;
}) {
  const all = source.items.every((r) => selected.has(r.id));
  const some = source.items.some((r) => selected.has(r.id));
  return (
    <section>
      <header className="mb-3 flex items-center gap-3 px-5">
        <Checkbox
          checked={all ? true : some ? "indeterminate" : false}
          onCheckedChange={(on) => onSelect(source.items.map((r) => r.id), on === true)}
          aria-label={`Select all from ${source.label} ${source.detail}`}
        />
        <span className="ml-1 text-text-2">{source.icon}</span>
        <h3 className="text-sm font-semibold">{source.label}</h3>
        <span className="mono min-w-0 truncate text-xs text-text-3" title={source.detail}>
          {source.detail}
        </span>
        <span className="ml-auto shrink-0 text-sm text-text-3">{plural(source.items.length, "item")}</span>
      </header>
      <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel">
        {source.items.map((r) => (
          <RecordRow key={r.id} record={r} checked={selected.has(r.id)} onCheck={(on) => onSelect([r.id], on)} origin={origin(r)} />
        ))}
      </ul>
    </section>
  );
}

export function InboxPage() {
  const [params, setParams] = useSearchParams();
  const [triage, setTriage] = useState<Triage>("untriaged");
  const [kind, setKind] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetStatus, setTargetStatus] = useState<TaskStatus>("backlog");
  const [targetWorkspace, setTargetWorkspace] = useState<string>("");

  const { data: workspaces = [] } = useWorkspaces();
  const { data: connections = [] } = useConnections();
  const projects = useMemo(() => projectsOf(workspaces, connections), [workspaces, connections]);
  const current = projects.find((p) => p.id === params.get("project")) ?? (projects.length === 1 ? projects[0] : undefined);
  const { data, isLoading, error, isFetching } = useRecords({
    triage,
    kind: kind || undefined,
    q: q || undefined,
    workspace_id: current?.workspace ? current.id : undefined,
    connection_id: current && !current.workspace ? current.id : undefined,
  });
  const importRecords = useImportRecords();
  const dismiss = useDismissRecords();
  const { scan } = useWorkspaceMutations();
  const { sync } = useConnectionMutations();

  const records = data?.items ?? [];
  const linkedWorkspace = (r: SourceRecord) => r.workspace_id ?? connections.find((c) => c.id === r.connection_id)?.workspace_id ?? null;
  const origin = (r: SourceRecord) =>
    r.workspace_id ? (workspaces.find((w) => w.id === r.workspace_id)?.name ?? "workspace") : (r.repo ?? connections.find((c) => c.id === r.connection_id)?.name ?? "GitHub");
  // Each project with its records, in tab order; records of a project no longer listed go last.
  const projectOf = (r: SourceRecord) => linkedWorkspace(r) ?? r.connection_id ?? "";
  const groups = [
    ...projects.map((p) => ({ id: p.id, name: p.name, items: records.filter((r) => projectOf(r) === p.id) })),
    { id: "", name: "Other", items: records.filter((r) => !projects.some((p) => p.id === projectOf(r))) },
  ].filter((g) => g.items.length > 0);

  const selectedRecords = records.filter((r) => selected.has(r.id));
  const needsWorkspace = selectedRecords.some((r) => !linkedWorkspace(r));
  const select = (ids: string[], on: boolean) => {
    const next = new Set(selected);
    for (const id of ids) {
      if (on) next.add(id);
      else next.delete(id);
    }
    setSelected(next);
  };

  const rescan = async () => {
    for (const p of current ? [current] : projects) {
      if (p.workspace) await scan.mutateAsync(p.workspace.id).catch(() => {});
      for (const c of p.connections) await sync.mutateAsync(c.id).catch(() => {});
    }
  };

  const counts = data?.projects ?? {};
  const tabs = [
    ...(projects.length > 1 ? [{ value: "all", label: "All projects", count: Object.values(counts).reduce((a, b) => a + b, 0) }] : []),
    ...projects.map((p) => ({
      value: p.id,
      label: (
        <>
          {p.workspace ? <FolderGit2 className="size-4" /> : <GithubMark className="size-4" />}
          {p.name}
        </>
      ),
      count: counts[p.id] ?? 0,
    })),
  ];

  const body = (
    <>
      <div className="flex flex-wrap items-center gap-3 px-8 py-5">
        <Segmented
          value={triage}
          onChange={(v) => {
            setTriage(v);
            setSelected(new Set());
          }}
          options={[
            { value: "untriaged", label: `To triage ${data?.counts.untriaged ?? ""}` },
            { value: "imported", label: `Imported ${data?.counts.imported ?? ""}` },
            { value: "dismissed", label: `Dismissed ${data?.counts.dismissed ?? ""}` },
          ]}
        />
        <NativeSelect value={kind} onChange={(e) => setKind(e.target.value)} className="w-44" aria-label="Type">
          <option value="">All types</option>
          <option value="code_todo">Code comments</option>
          <option value="markdown_task">Markdown tasks</option>
          <option value="todo_file">TODO files</option>
          <option value="github_issue">GitHub issues</option>
          <option value="github_pr">Pull requests</option>
        </NativeSelect>
        <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" aria-label="Search" className="w-64" />
        {isFetching && <Spinner className="size-4" />}
        <span className="ml-auto text-sm text-text-3">{data ? plural(data.total, "item") : ""}</span>
      </div>

      {selected.size > 0 && (
        <div className="mx-8 mb-4 flex flex-wrap items-center gap-3 rounded-panel bg-panel px-5 py-3">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button size="xs" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {triage !== "dismissed" && (
              <>
                <span className="text-sm text-text-2">Create tasks in</span>
                <NativeSelect value={targetStatus} onChange={(e) => setTargetStatus(e.target.value as TaskStatus)} className="h-9 w-32" aria-label="Target column">
                  <option value="backlog">Backlog</option>
                  <option value="ready">Ready</option>
                </NativeSelect>
                {needsWorkspace && (
                  <NativeSelect value={targetWorkspace} onChange={(e) => setTargetWorkspace(e.target.value)} className="h-9 w-56" aria-label="Workspace for GitHub items">
                    <option value="">Workspace for GitHub items…</option>
                    {workspaces.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </NativeSelect>
                )}
                <Button
                  size="sm"
                  variant="primary"
                  disabled={importRecords.isPending}
                  onClick={() =>
                    importRecords.mutate(
                      { record_ids: [...selected], status: targetStatus, workspace_id: targetWorkspace || null },
                      { onSuccess: () => setSelected(new Set()) },
                    )
                  }
                >
                  <ListPlus /> Create {selected.size} task{selected.size === 1 ? "" : "s"}
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => dismiss.mutate({ record_ids: [...selected], dismissed: triage !== "dismissed" }, { onSuccess: () => setSelected(new Set()) })}
            >
              {triage === "dismissed" ? (
                <>
                  <Undo2 /> Restore
                </>
              ) : (
                <>
                  <EyeOff /> Dismiss
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading && <div className="flex justify-center p-10"><Spinner /></div>}
        {error && <div className="px-8 py-4"><ErrorNote title="Discovered work could not be loaded">{(error as Error).message}</ErrorNote></div>}
        {!isLoading && records.length === 0 && (
          <EmptyState
            icon={Inbox}
            title={`${triage === "untriaged" ? "Nothing waiting for triage" : triage === "imported" ? "Nothing imported yet" : "Nothing dismissed"}${current ? ` in ${current.name}` : ""}`}
            action={
              triage === "untriaged" &&
              !current && (
                <Button variant="secondary" asChild>
                  <Link to="/integrations">Add workspaces or GitHub repositories</Link>
                </Button>
              )
            }
          >
            {triage === "untriaged" && !current ? "TODO and FIXME comments, unchecked Markdown tasks, TODO files and GitHub issues appear here." : undefined}
          </EmptyState>
        )}
        <div className="space-y-14 px-8 pt-1 pb-12">
          {current ? (
            <div className="space-y-8">
              {sourcesOf(records, workspaces, connections).map((s) => (
                <SourceSection key={s.key} source={s} selected={selected} onSelect={select} origin={origin} />
              ))}
            </div>
          ) : (
            groups.map((g) => (
              <section key={g.id}>
                <h2 className="mb-4 px-5 text-md font-semibold">{g.name}</h2>
                <div className="space-y-8">
                  {sourcesOf(g.items, workspaces, connections).map((s) => (
                    <SourceSection key={s.key} source={s} selected={selected} onSelect={select} origin={origin} />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </>
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Inbox"
        actions={
          <Button variant="secondary" onClick={() => void rescan()} disabled={scan.isPending || sync.isPending}>
            <RefreshCw className={cn((scan.isPending || sync.isPending) && "animate-spin")} /> Rescan
          </Button>
        }
      />
      {projects.length > 0 ? (
        <Tabs
          value={current?.id ?? "all"}
          onValueChange={(v) => {
            setParams(v === "all" ? {} : { project: v }, { replace: true });
            setSelected(new Set());
          }}
          tabs={tabs}
          className="flex-1"
        >
          <TabPanel value={current?.id ?? "all"} className="flex flex-col overflow-hidden">
            {body}
          </TabPanel>
        </Tabs>
      ) : (
        body
      )}
    </div>
  );
}
