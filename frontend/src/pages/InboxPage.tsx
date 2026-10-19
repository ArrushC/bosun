import { ChevronDown, ChevronRight, EyeOff, Inbox, ListPlus, RefreshCw, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useConnectionMutations, useConnections, useDismissRecords, useImportRecords, useRecords, useWorkspaceMutations, useWorkspaces } from "@/api/hooks";
import type { SourceRecord, TaskStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Md, SOURCE_ICON, SourceIcon, Spinner } from "@/components/ui/display";
import { Checkbox, NativeSelect, SearchInput, Segmented } from "@/components/ui/inputs";
import { cn, relativeTime } from "@/lib/utils";

type Triage = "untriaged" | "imported" | "dismissed";

function location(r: SourceRecord) {
  if (r.repo) return `${r.repo}#${r.number}`;
  return `${r.path}:${r.line}`;
}

function RecordRow({ record, checked, onCheck, origin }: { record: SourceRecord; checked: boolean; onCheck: (v: boolean) => void; origin: string }) {
  const [open, setOpen] = useState(false);
  const meta = record.meta as Record<string, unknown>;
  return (
    <li className={cn("border-b border-line-soft last:border-b-0", checked && "bg-accent-soft")}>
      <div className="flex items-center gap-3 px-3 py-2">
        <Checkbox checked={checked} onCheckedChange={onCheck} aria-label={`Select ${record.title}`} />
        <button onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left" aria-expanded={open}>
          {open ? <ChevronDown className="size-3.5 shrink-0 text-text-3" /> : <ChevronRight className="size-3.5 shrink-0 text-text-3" />}
          <SourceIcon kind={record.kind} />
          <span className="min-w-0 flex-1 truncate text-sm">{record.title}</span>
          {record.tag && <span className="mono shrink-0 rounded-[4px] border border-line-soft px-1 text-2xs text-text-2">{record.tag}</span>}
          {record.labels.slice(0, 3).map((l) => (
            <span key={l} className="hidden shrink-0 rounded-[4px] border border-line-soft px-1.5 text-2xs text-text-2 md:inline">
              {l}
            </span>
          ))}
          <span className="mono hidden w-60 shrink-0 truncate text-right text-xs text-text-3 lg:inline" title={location(record)}>
            {location(record)}
          </span>
          <span className="w-20 shrink-0 text-right text-xs text-text-3">{relativeTime(record.external_updated_at ?? record.first_seen_at)}</span>
        </button>
        {record.task_ids.length > 0 && (
          <Link to={`/board?task=${record.task_ids[0]}`} className="shrink-0 text-xs text-accent-strong hover:underline">
            View task
          </Link>
        )}
      </div>
      {open && (
        <div className="space-y-2 px-11 pb-3">
          <p className="text-xs text-text-3">
            {SOURCE_ICON[record.kind]?.label} in {origin}
            {meta.section ? `, section "${String(meta.section)}"` : ""}
            {meta.owner ? `, owner ${String(meta.owner)}` : ""}
            {record.author ? `, opened by @${record.author}` : ""}
            {meta.head ? `, branch ${String(meta.head)} into ${String(meta.base)}` : ""}
          </p>
          {record.context && (
            <pre className="overflow-x-auto rounded-[6px] border border-line-soft bg-sunken px-3 py-2 font-mono text-[11.5px] leading-[17px] text-text-2">{record.context}</pre>
          )}
          {record.body && record.kind.startsWith("github") && (
            <div className="max-h-72 overflow-y-auto rounded-[6px] border border-line-soft px-3 py-2">
              <Md className="text-[13px]">{record.body}</Md>
            </div>
          )}
          {record.url && (
            <a href={record.url} target="_blank" rel="noreferrer noopener" className="text-xs text-accent-strong hover:underline">
              Open on GitHub
            </a>
          )}
        </div>
      )}
    </li>
  );
}

export function InboxPage() {
  const [triage, setTriage] = useState<Triage>("untriaged");
  const [kind, setKind] = useState<string>("");
  const [origin, setOrigin] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetStatus, setTargetStatus] = useState<TaskStatus>("backlog");
  const [targetWorkspace, setTargetWorkspace] = useState<string>("");

  const { data: workspaces = [] } = useWorkspaces();
  const { data: connections = [] } = useConnections();
  const [originType, originId] = origin.split(":");
  const { data, isLoading, error, isFetching } = useRecords({
    triage,
    kind: kind || undefined,
    q: q || undefined,
    workspace_id: originType === "ws" ? originId : undefined,
    connection_id: originType === "conn" ? originId : undefined,
  });
  const importRecords = useImportRecords();
  const dismiss = useDismissRecords();
  const { scan } = useWorkspaceMutations();
  const { sync } = useConnectionMutations();

  const records = data?.items ?? [];
  const originName = (r: SourceRecord) =>
    r.workspace_id ? (workspaces.find((w) => w.id === r.workspace_id)?.name ?? "workspace") : (connections.find((c) => c.id === r.connection_id)?.name ?? r.repo ?? "GitHub");
  const groups = useMemo(() => {
    const map = new Map<string, SourceRecord[]>();
    for (const r of records) {
      const key = r.workspace_id ? `ws:${r.workspace_id}` : `conn:${r.connection_id}`;
      map.set(key, [...(map.get(key) ?? []), r]);
    }
    return [...map.entries()];
  }, [records]);

  const selectedRecords = records.filter((r) => selected.has(r.id));
  const needsWorkspace = selectedRecords.some((r) => !r.workspace_id);
  const toggle = (id: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  };

  const rescan = async () => {
    for (const w of workspaces) if (!originType || (originType === "ws" && originId === w.id)) await scan.mutateAsync(w.id).catch(() => {});
    for (const c of connections) if (!originType || (originType === "conn" && originId === c.id)) await sync.mutateAsync(c.id).catch(() => {});
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Inbox"
        subtitle="Work discovered in your workspaces and on GitHub. Nothing becomes a task, or runs, until you promote it."
        actions={
          <Button size="sm" variant="secondary" onClick={() => void rescan()} disabled={scan.isPending || sync.isPending}>
            <RefreshCw className={cn((scan.isPending || sync.isPending) && "animate-spin")} /> Rescan sources
          </Button>
        }
      >
        <div className="flex flex-wrap items-center gap-2 px-5 pb-2.5">
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
          <NativeSelect value={kind} onChange={(e) => setKind(e.target.value)} className="w-44" aria-label="Source type">
            <option value="">All source types</option>
            <option value="code_todo">Code annotations</option>
            <option value="markdown_task">Markdown tasks</option>
            <option value="todo_file">TODO files</option>
            <option value="github_issue">GitHub issues</option>
            <option value="github_pr">Pull requests</option>
          </NativeSelect>
          <NativeSelect value={origin} onChange={(e) => setOrigin(e.target.value)} className="w-48" aria-label="Origin">
            <option value="">Every workspace and repo</option>
            {workspaces.map((w) => (
              <option key={w.id} value={`ws:${w.id}`}>
                {w.name}
              </option>
            ))}
            {connections.map((c) => (
              <option key={c.id} value={`conn:${c.id}`}>
                {c.name}
              </option>
            ))}
          </NativeSelect>
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search discovered work" aria-label="Search" className="w-60" />
          {isFetching && <Spinner className="size-3.5" />}
          <span className="ml-auto text-xs text-text-3">{data ? `${data.total} items` : ""}</span>
        </div>
      </PageHeader>

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line-soft bg-panel px-5 py-2">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button size="xs" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {triage !== "dismissed" && (
              <>
                <span className="text-xs text-text-2">Create tasks in</span>
                <NativeSelect value={targetStatus} onChange={(e) => setTargetStatus(e.target.value as TaskStatus)} className="h-7 w-28 text-xs" aria-label="Target column">
                  <option value="backlog">Backlog</option>
                  <option value="ready">Ready</option>
                </NativeSelect>
                {needsWorkspace && (
                  <NativeSelect value={targetWorkspace} onChange={(e) => setTargetWorkspace(e.target.value)} className="h-7 w-44 text-xs" aria-label="Workspace for GitHub items">
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
        {error && <div className="p-5"><ErrorNote title="Discovered work could not be loaded">{(error as Error).message}</ErrorNote></div>}
        {!isLoading && records.length === 0 && (
          <EmptyState
            icon={Inbox}
            title={triage === "untriaged" ? "Nothing waiting for triage" : triage === "imported" ? "Nothing imported yet" : "Nothing dismissed"}
            action={
              triage === "untriaged" && (
                <Button variant="secondary" asChild>
                  <Link to="/integrations">Add workspaces or GitHub repositories</Link>
                </Button>
              )
            }
          >
            {triage === "untriaged" ? "TODO and FIXME comments, unchecked Markdown tasks, TODO files and GitHub issues appear here." : undefined}
          </EmptyState>
        )}
        <div className="space-y-5 px-5 py-4">
          {groups.map(([key, items]) => {
            const all = items.every((r) => selected.has(r.id));
            const some = items.some((r) => selected.has(r.id));
            return (
              <section key={key}>
                <header className="mb-1.5 flex items-center gap-3 px-3">
                  <Checkbox
                    checked={all ? true : some ? "indeterminate" : false}
                    onCheckedChange={(on) => {
                      const next = new Set(selected);
                      for (const r of items) {
                        if (on) next.add(r.id);
                        else next.delete(r.id);
                      }
                      setSelected(next);
                    }}
                    aria-label={`Select all from ${originName(items[0])}`}
                  />
                  <h2 className="text-sm font-semibold">{originName(items[0])}</h2>
                  <span className="text-xs text-text-3">{items.length}</span>
                </header>
                <ul className="rounded-[10px] border border-line-soft">
                  {items.map((r) => (
                    <RecordRow key={r.id} record={r} checked={selected.has(r.id)} onCheck={(on) => toggle(r.id, on)} origin={originName(r)} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
