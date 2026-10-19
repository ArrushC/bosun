import { ArrowDownWideNarrow, ListFilter, RotateCcw, Rows3 } from "lucide-react";
import { useEffect, useRef } from "react";

import { useLabels, useWorkspaces } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { SOURCE_ICON } from "@/components/ui/display";
import { SearchInput, Segmented } from "@/components/ui/inputs";
import { DropdownCheckbox, DropdownContent, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger } from "@/components/ui/overlays";
import { PRIORITY } from "@/lib/status";
import { cn } from "@/lib/utils";
import { type GroupBy, type SortBy, useUi } from "@/stores/ui";

export function BoardToolbar({ total, shown }: { total: number; shown: number }) {
  const { board, setBoard, resetBoard } = useUi();
  const { data: labels = [] } = useLabels();
  const { data: workspaces = [] } = useWorkspaces();
  const search = useRef<HTMLInputElement>(null);
  const activeFilters =
    board.labels.length + board.priorities.length + board.sources.length + (board.workspaceId ? 1 : 0) + (board.agentState !== "any" ? 1 : 0) + (board.showDone ? 0 : 1);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        e.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggle = (list: string[], value: string) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  return (
    <div className="flex flex-wrap items-center gap-2 px-5 pb-2.5">
      <SearchInput
        ref={search}
        value={board.q}
        onChange={(e) => setBoard({ q: e.target.value })}
        placeholder="Filter tasks  /"
        aria-label="Filter tasks"
        className="w-64"
      />
      <Segmented
        value={board.agentState}
        onChange={(v) => setBoard({ agentState: v })}
        options={[
          { value: "any", label: "All" },
          { value: "running", label: "Agents running" },
          { value: "attention", label: "Needs attention" },
        ]}
      />
      <DropdownMenu>
        <DropdownTrigger asChild>
          <Button size="sm" variant="outline" className={cn(activeFilters > 0 && "border-accent text-text")}>
            <ListFilter /> Filters{activeFilters > 0 && ` (${activeFilters})`}
          </Button>
        </DropdownTrigger>
        <DropdownContent align="start">
          <DropdownLabel>Priority</DropdownLabel>
          {Object.entries(PRIORITY).map(([value, p]) => (
            <DropdownCheckbox key={value} checked={board.priorities.includes(value)} onCheckedChange={() => setBoard({ priorities: toggle(board.priorities, value) })}>
              {p.label}
            </DropdownCheckbox>
          ))}
          <DropdownSeparator />
          <DropdownLabel>Source</DropdownLabel>
          {Object.entries(SOURCE_ICON).map(([value, s]) => (
            <DropdownCheckbox key={value} checked={board.sources.includes(value)} onCheckedChange={() => setBoard({ sources: toggle(board.sources, value) })}>
              {s.label}
            </DropdownCheckbox>
          ))}
          {workspaces.length > 1 && (
            <>
              <DropdownSeparator />
              <DropdownLabel>Workspace</DropdownLabel>
              {workspaces.map((w) => (
                <DropdownCheckbox key={w.id} checked={board.workspaceId === w.id} onCheckedChange={(v) => setBoard({ workspaceId: v ? w.id : null })}>
                  {w.name}
                </DropdownCheckbox>
              ))}
            </>
          )}
          {labels.length > 0 && (
            <>
              <DropdownSeparator />
              <DropdownLabel>Labels</DropdownLabel>
              <div className="max-h-48 overflow-y-auto">
                {labels.map((l) => (
                  <DropdownCheckbox key={l} checked={board.labels.includes(l)} onCheckedChange={() => setBoard({ labels: toggle(board.labels, l) })}>
                    {l}
                  </DropdownCheckbox>
                ))}
              </div>
            </>
          )}
          <DropdownSeparator />
          <DropdownCheckbox checked={board.showDone} onCheckedChange={(v) => setBoard({ showDone: v })}>
            Show done tasks
          </DropdownCheckbox>
        </DropdownContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownTrigger asChild>
          <Button size="sm" variant="outline" aria-label="Group and sort">
            <Rows3 /> {board.groupBy === "none" ? "No grouping" : `By ${board.groupBy}`}
          </Button>
        </DropdownTrigger>
        <DropdownContent align="start">
          <DropdownLabel>Group rows by</DropdownLabel>
          {(["none", "priority", "workspace", "kind"] as GroupBy[]).map((g) => (
            <DropdownCheckbox key={g} checked={board.groupBy === g} onCheckedChange={() => setBoard({ groupBy: g })}>
              {g === "none" ? "No grouping" : g === "kind" ? "Type" : g[0].toUpperCase() + g.slice(1)}
            </DropdownCheckbox>
          ))}
          <DropdownSeparator />
          <DropdownLabel>
            <span className="inline-flex items-center gap-1">
              <ArrowDownWideNarrow className="size-3" /> Order cards by
            </span>
          </DropdownLabel>
          {(["manual", "priority", "updated", "created"] as SortBy[]).map((s) => (
            <DropdownCheckbox key={s} checked={board.sortBy === s} onCheckedChange={() => setBoard({ sortBy: s })}>
              {{ manual: "Manual (drag to reorder)", priority: "Priority", updated: "Recent activity", created: "Newest" }[s]}
            </DropdownCheckbox>
          ))}
        </DropdownContent>
      </DropdownMenu>

      {(activeFilters > 0 || board.q) && (
        <Button size="sm" variant="ghost" onClick={resetBoard}>
          <RotateCcw /> Clear
        </Button>
      )}
      <span className="ml-auto text-xs text-text-3">{shown === total ? `${total} tasks` : `${shown} of ${total} tasks`}</span>
    </div>
  );
}
