import { FileDiff, GitCommitHorizontal, GitMerge, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { useDiff, useRunAction } from "@/api/hooks";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Notice, Spinner } from "@/components/ui/display";
import { Dialog } from "@/components/ui/overlays";
import { cn } from "@/lib/utils";

interface FilePatch {
  path: string;
  lines: string[];
}

function splitPatch(patch: string): FilePatch[] {
  const files: FilePatch[] = [];
  let current: FilePatch | null = null;
  for (const line of patch.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const match = line.match(/ b\/(.+)$/);
      current = { path: match?.[1] ?? line, lines: [] };
      files.push(current);
    } else if (current && !line.startsWith("index ") && !line.startsWith("--- ") && !line.startsWith("+++ ")) {
      current.lines.push(line);
    }
  }
  return files;
}

const STATUS_LABEL: Record<string, string> = { A: "added", M: "modified", D: "deleted", R: "renamed" };

export function DiffView({ run }: { run: Run }) {
  const { data: diff, isLoading, error } = useDiff(run.id);
  const action = useRunAction(run.id);
  const [confirm, setConfirm] = useState<"merge" | "discard" | null>(null);
  const files = useMemo(() => splitPatch(diff?.patch ?? ""), [diff?.patch]);
  const [selected, setSelected] = useState<string | null>(null);

  if (isLoading) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  if (error) return <div className="px-8 py-5"><ErrorNote title="Changes could not be loaded">{(error as Error).message}</ErrorNote></div>;
  if (!diff?.available) {
    return (
      <EmptyState icon={FileDiff} title="No reviewable diff">
        {diff?.reason ?? "This run has no isolated worktree."}
      </EmptyState>
    );
  }
  const integrated = run.integration_status === "merged" || run.integration_status === "discarded";
  const shown = selected ? files.filter((f) => f.path === selected) : files;

  return (
    <div className="flex h-full min-h-0">
      <aside className="w-80 shrink-0 overflow-y-auto border-r border-line-soft p-5">
        <div className="space-y-3">
          <p className="text-sm">
            <span className="mono text-sm text-text-2">{diff.branch}</span>
            <span className="mt-0.5 block text-xs text-text-3">based on {diff.base_branch ?? "HEAD"}</span>
          </p>
          <p className="text-sm">
            <span className="text-st-ok">+{diff.additions}</span> <span className="text-st-fail">−{diff.deletions}</span>{" "}
            <span className="text-text-3">in {diff.files.length} files</span>
          </p>
          {!integrated && !run.active && (
            <div className="flex flex-col gap-2 pt-2">
              <Button size="sm" variant="primary" onClick={() => setConfirm("merge")} disabled={!diff.files.length}>
                <GitMerge /> Merge into {diff.base_branch ?? "your branch"}
              </Button>
              <Button size="sm" variant="danger" onClick={() => setConfirm("discard")}>
                <Trash2 /> Discard worktree
              </Button>
            </div>
          )}
          {integrated && <Notice>Changes were {run.integration_status}.</Notice>}
          {run.active && <Notice>Changes can be merged once the run stops.</Notice>}
        </div>
        <h3 className="mt-6 mb-2 text-sm font-semibold">Files</h3>
        <ul className="space-y-1">
          <li>
            <button onClick={() => setSelected(null)} className={cn("w-full rounded-control px-3 py-2 text-left text-sm hover:bg-raised", !selected && "bg-raised")}>
              All files
            </button>
          </li>
          {diff.files.map((f) => (
            <li key={String(f.path)}>
              <button
                onClick={() => setSelected(String(f.path))}
                className={cn("flex w-full items-center gap-2 rounded-control px-3 py-2 text-left text-xs hover:bg-raised", selected === f.path && "bg-raised")}
                title={`${STATUS_LABEL[String(f.status)] ?? f.status}: ${f.path}`}
              >
                <span className="mono min-w-0 flex-1 truncate">{String(f.path)}</span>
                <span className="text-st-ok">+{String(f.additions)}</span>
                <span className="text-st-fail">−{String(f.deletions)}</span>
              </button>
            </li>
          ))}
        </ul>
        {diff.commits.length > 0 && (
          <>
            <h3 className="mt-6 mb-2 text-sm font-semibold">Commits</h3>
            <ul className="space-y-3">
              {diff.commits.map((c) => (
                <li key={String(c.sha)} className="flex gap-2.5 text-sm">
                  <GitCommitHorizontal className="mt-0.5 size-3.5 shrink-0 text-text-3" />
                  <span className="min-w-0">
                    <span className="block truncate">{String(c.subject)}</span>
                    <span className="mono text-xs text-text-3">{String(c.sha).slice(0, 8)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>
      <div className="min-w-0 flex-1 overflow-auto p-6">
        {diff.truncated && <Notice tone="warn" className="mb-3">The diff is large and was truncated.</Notice>}
        {shown.length === 0 && <EmptyState icon={FileDiff} title="No changes">The agents did not change any files.</EmptyState>}
        <div className="space-y-4">
          {shown.map((f) => (
            <section key={f.path} className="overflow-hidden rounded-[14px] border border-line-soft">
              <header className="mono border-b border-line-soft bg-raised px-4 py-2.5 text-xs">{f.path}</header>
              <pre className="overflow-x-auto font-mono text-xs leading-[20px]">
                {f.lines.map((line, i) => (
                  <div
                    key={i}
                    className={cn(
                      "px-3 whitespace-pre",
                      line.startsWith("+") && "bg-st-ok/10 text-text",
                      line.startsWith("-") && "bg-st-fail/10 text-text",
                      line.startsWith("@@") && "bg-sunken text-text-3",
                      !line.startsWith("+") && !line.startsWith("-") && !line.startsWith("@@") && "text-text-2",
                    )}
                  >
                    {line || " "}
                  </div>
                ))}
              </pre>
            </section>
          ))}
        </div>
      </div>

      <Dialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
        size="sm"
        title={confirm === "merge" ? `Merge into ${diff.base_branch ?? "your branch"}?` : "Discard these changes?"}
        description={
          confirm === "merge"
            ? `Runs "git merge --no-ff ${diff.branch}" in ${run.workspace_path}. Your checkout must be clean and on ${diff.base_branch}. Nothing is pushed.`
            : "Removes the run's worktree and branch. The changes cannot be recovered."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant={confirm === "merge" ? "primary" : "danger"}
              disabled={action.isPending}
              onClick={() =>
                action.mutate({ action: "integrate", body: { action: confirm, confirm: true } }, { onSuccess: () => setConfirm(null) })
              }
            >
              {confirm === "merge" ? "Merge" : "Discard"}
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-2">{diff.files.length} files, {diff.commits.length} commits on {diff.branch}.</p>
      </Dialog>
    </div>
  );
}
