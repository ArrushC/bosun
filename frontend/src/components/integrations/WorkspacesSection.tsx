import { ChevronRight, CornerLeftUp, Folder, FolderGit2, FolderPlus, ScanSearch, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";

import { useBrowse, useMeta, useSettings, useWorkspaceMutations, useWorkspaces } from "@/api/hooks";
import type { Workspace } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState, Spinner } from "@/components/ui/display";
import { Checkbox, Field, Input, Switch, Textarea } from "@/components/ui/inputs";
import { Dialog } from "@/components/ui/overlays";
import { cn, relativeTime } from "@/lib/utils";

function AddWorkspaceDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { data: meta } = useMeta();
  const [path, setPath] = useState<string | null>(null);
  const [manual, setManual] = useState("");
  const [trusted, setTrusted] = useState(false);
  const browse = useBrowse(path ?? meta?.initial_workspace ?? null);
  const { create } = useWorkspaceMutations();
  const target = manual.trim() || browse.data?.path || "";

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title="Add a workspace"
      description="Pick a project directory. Bosun scans it for TODOs and runs agents inside isolated git worktrees of it."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!target || create.isPending} onClick={() => create.mutate({ path: target, trusted }, { onSuccess: () => onOpenChange(false) })}>
            {create.isPending ? "Scanning…" : "Add and scan"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="overflow-hidden rounded-[12px] border border-line-soft">
          <div className="flex items-center gap-2 border-b border-line-soft px-3 py-2">
            <Button size="icon-sm" variant="ghost" aria-label="Parent directory" disabled={!browse.data?.parent} onClick={() => setPath(browse.data?.parent ?? null)}>
              <CornerLeftUp />
            </Button>
            <span className="mono min-w-0 flex-1 truncate text-xs">{browse.data?.path ?? "…"}</span>
            {browse.isFetching && <Spinner className="size-3.5" />}
          </div>
          <ul className="max-h-72 overflow-y-auto p-1.5">
            {browse.data?.entries.map((e) => (
              <li key={e.path}>
                <button onClick={() => setPath(e.path)} className="flex w-full items-center gap-2.5 rounded-[8px] px-3 py-2 text-left text-sm hover:bg-raised">
                  {e.is_git ? <FolderGit2 className="size-4 text-accent" /> : <Folder className="size-4 text-text-3" />}
                  <span className="flex-1 truncate">{e.name}</span>
                  {e.is_git && <span className="text-xs text-text-3">git</span>}
                  <ChevronRight className="size-3.5 text-text-3" />
                </button>
              </li>
            ))}
            {browse.data && browse.data.entries.length === 0 && <li className="px-3 py-3 text-sm text-text-3">No sub-directories.</li>}
          </ul>
        </div>
        <Field label="Or paste an absolute path">
          <Input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="/home/you/code/project" />
        </Field>
        <label className="flex items-start gap-3 text-sm">
          <Checkbox checked={trusted} onCheckedChange={setTrusted} aria-label="Trust this workspace" className="mt-0.5" />
          <span>
            Trust this workspace
            <span className="mt-0.5 block text-sm text-text-3">
              Agents load the repository's own .claude/settings.json, hooks and CLAUDE.md. Only enable for code you trust; otherwise CLAUDE.md is passed as read-only guidance.
            </span>
          </span>
        </label>
        <p className="text-sm text-text-2">
          Selected: <span className="mono">{target || "nothing yet"}</span>
        </p>
      </div>
    </Dialog>
  );
}

function ScanSettingsDialog({ workspace, onClose }: { workspace: Workspace; onClose: () => void }) {
  const { data: settings } = useSettings();
  const { update } = useWorkspaceMutations();
  const defaults = settings?.scan;
  const ws = workspace.scan_settings as Record<string, unknown>;
  const [tags, setTags] = useState(((ws.tags as string[] | undefined) ?? defaults?.tags ?? []).join(", "));
  const [excludes, setExcludes] = useState(((ws.exclude_globs as string[] | undefined) ?? defaults?.exclude_globs ?? []).join("\n"));
  const [maxKb, setMaxKb] = useState(String(ws.max_file_kb ?? defaults?.max_file_kb ?? 512));
  const [markdown, setMarkdown] = useState(Boolean(ws.include_markdown_tasks ?? defaults?.include_markdown_tasks ?? true));
  const [todoFiles, setTodoFiles] = useState(Boolean(ws.include_todo_files ?? defaults?.include_todo_files ?? true));

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={`Scan settings for ${workspace.name}`}
      description="Overrides the global defaults for this workspace only. .gitignore is always respected."
      footer={
        <>
          <Button variant="ghost" onClick={() => update.mutate({ id: workspace.id, body: { scan_settings: {} } }, { onSuccess: onClose })}>
            Use defaults
          </Button>
          <Button
            variant="primary"
            onClick={() =>
              update.mutate(
                {
                  id: workspace.id,
                  body: {
                    scan_settings: {
                      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
                      exclude_globs: excludes.split("\n").map((t) => t.trim()).filter(Boolean),
                      max_file_kb: Number(maxKb) || 512,
                      include_markdown_tasks: markdown,
                      include_todo_files: todoFiles,
                    },
                  },
                },
                { onSuccess: onClose },
              )
            }
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        <Field label="Annotation tags" hint="Comma separated, case sensitive.">
          <Input value={tags} onChange={(e) => setTags(e.target.value)} />
        </Field>
        <Field label="Excluded paths" hint="One gitignore-style pattern per line.">
          <Textarea rows={8} value={excludes} onChange={(e) => setExcludes(e.target.value)} className="font-mono text-xs leading-6" />
        </Field>
        <div className="grid grid-cols-3 items-end gap-5">
          <Field label="Max file size (KB)">
            <Input type="number" min={1} value={maxKb} onChange={(e) => setMaxKb(e.target.value)} />
          </Field>
          <label className="flex h-10 items-center gap-2.5 text-sm">
            <Switch checked={markdown} onCheckedChange={setMarkdown} label="Markdown tasks" /> Markdown tasks
          </label>
          <label className="flex h-10 items-center gap-2.5 text-sm">
            <Switch checked={todoFiles} onCheckedChange={setTodoFiles} label="TODO files" /> TODO files
          </label>
        </div>
      </div>
    </Dialog>
  );
}

export function WorkspacesSection() {
  const { data: workspaces = [], isLoading } = useWorkspaces();
  const { scan, update, remove } = useWorkspaceMutations();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Workspace | null>(null);
  const [scanning, setScanning] = useState<string | null>(null);

  return (
    <section className="space-y-5">
      <header className="flex items-center gap-4">
        <div>
          <h2 className="text-md font-semibold">Workspaces</h2>
          <p className="mt-1 text-sm text-text-2">Project folders Bosun scans for work and where agents run.</p>
        </div>
        <Button variant="primary" className="ml-auto" onClick={() => setAdding(true)}>
          <FolderPlus /> Add workspace
        </Button>
      </header>
      {isLoading && <Spinner />}
      {!isLoading && workspaces.length === 0 && (
        <EmptyState icon={FolderGit2} title="No workspaces yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add workspace</Button>}>
          Add the project you want agents to work on.
        </EmptyState>
      )}
      <ul className="divide-y divide-line-soft overflow-hidden rounded-panel bg-panel empty:hidden">
        {workspaces.map((w) => {
          const summary = w.last_scan_summary as { files_scanned?: number; duration_ms?: number; truncated?: boolean; used_git?: boolean } | null;
          return (
            <li key={w.id} className="grid gap-4 px-5 py-4 md:grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <p className="flex items-center gap-2.5 text-base font-medium">
                  <FolderGit2 className={cn("size-[18px]", w.status === "missing" ? "text-st-fail" : w.is_git ? "text-accent" : "text-text-3")} />
                  {w.name}
                  {w.is_demo && <span className="rounded-full bg-raised px-2 text-xs font-normal text-text-2">demo</span>}
                  {w.status === "missing" && <span className="text-sm font-normal text-st-fail">directory missing</span>}
                </p>
                <p className="mono mt-1 truncate text-xs text-text-3">{w.path}</p>
                <p
                  className="mt-1.5 text-sm text-text-2"
                  title={summary?.files_scanned !== undefined ? `${summary.files_scanned} files in ${summary.duration_ms} ms` : undefined}
                >
                  {w.is_git ? "" : "Not a git repository, so agents edit in place. "}
                  {w.open_items} discovered, {w.task_count} tasks, {w.last_scanned_at ? `scanned ${relativeTime(w.last_scanned_at)}` : "not scanned yet"}
                  {summary?.truncated ? ". File limit reached; some files were skipped." : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <label className="mr-2 flex items-center gap-2 text-sm text-text-2" title="Load the repository's .claude settings, hooks and CLAUDE.md">
                  <Switch checked={w.trusted} onCheckedChange={(trusted) => update.mutate({ id: w.id, body: { trusted } })} label={`Trust ${w.name}`} /> Trusted
                </label>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={scanning === w.id}
                  onClick={() => {
                    setScanning(w.id);
                    scan.mutate(w.id, { onSettled: () => setScanning(null) });
                  }}
                >
                  <ScanSearch className={cn(scanning === w.id && "animate-pulse")} /> Scan
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label="Scan settings" onClick={() => setEditing(w)}>
                  <Settings2 />
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label={`Remove ${w.name}`} onClick={() => confirm(`Remove ${w.name}? Tasks stay; discovered items from it are removed.`) && remove.mutate(w.id)}>
                  <Trash2 />
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-sm leading-6 text-text-3">Bosun's own directory and data are never scanned. Files over the size limit, binaries, and symlinks leaving the workspace are skipped.</p>
      <AddWorkspaceDialog open={adding} onOpenChange={setAdding} />
      {editing && <ScanSettingsDialog workspace={editing} onClose={() => setEditing(null)} />}
    </section>
  );
}
