import { Command } from "cmdk";
import { Dialog as DialogPrimitive } from "radix-ui";
import { Gauge, History, Inbox, KanbanSquare, Plug, Plus, Settings2, Waypoints, Workflow } from "lucide-react";
import { useNavigate } from "react-router";

import { useRuns, useTasks } from "@/api/hooks";
import { StatusPill } from "@/components/ui/display";
import { runState } from "@/lib/status";
import { useUi } from "@/stores/ui";

const ITEM =
  "flex h-11 cursor-pointer items-center gap-3 rounded-[10px] px-3 text-sm text-text aria-selected:bg-raised [&_svg]:size-[18px] [&_svg]:text-text-2";

export function CommandPalette() {
  const { paletteOpen, setPalette, setCreate } = useUi();
  const navigate = useNavigate();
  const { data: tasks = [] } = useTasks();
  const { data: runs = [] } = useRuns();

  const go = (to: string) => {
    setPalette(false);
    navigate(to);
  };

  return (
    <DialogPrimitive.Root open={paletteOpen} onOpenChange={setPalette}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[rgb(6_9_15/0.55)]" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed top-[14vh] left-1/2 z-50 w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-[20px] border border-line-soft bg-panel shadow-pop"
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <Command loop label="Command palette">
            <Command.Input
              placeholder="Jump to a task, run or page…"
              className="h-14 w-full border-b border-line-soft bg-transparent px-5 text-base text-text placeholder:text-text-3 focus:outline-none"
            />
            <Command.List className="max-h-[52vh] overflow-y-auto p-2.5">
              <Command.Empty className="px-3 py-8 text-center text-sm text-text-3">Nothing matches.</Command.Empty>
              <Command.Group heading="Actions" className="text-xs text-text-3 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5">
                <Command.Item className={ITEM} onSelect={() => { setPalette(false); setCreate(true); }}>
                  <Plus /> Create a task
                </Command.Item>
              </Command.Group>
              <Command.Group heading="Pages" className="text-xs text-text-3 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5">
                <Command.Item className={ITEM} onSelect={() => go("/")}><Gauge /> Overview</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/board")}><KanbanSquare /> Board</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/workflows")}><Workflow /> Workflows</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/inbox")}><Inbox /> Inbox</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/graph")}><Waypoints /> Agent graph</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/runs")}><History /> Runs</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/integrations")}><Plug /> Integrations</Command.Item>
                <Command.Item className={ITEM} onSelect={() => go("/settings")}><Settings2 /> Settings</Command.Item>
              </Command.Group>
              {tasks.length > 0 && (
                <Command.Group heading="Tasks" className="text-xs text-text-3 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5">
                  {tasks.slice(0, 200).map((t) => (
                    <Command.Item key={t.id} value={`${t.key} ${t.title}`} className={ITEM} onSelect={() => go(`/board?task=${t.id}`)}>
                      <span className="mono w-14 shrink-0 text-text-3">{t.key}</span>
                      <span className="truncate">{t.title}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
              {runs.length > 0 && (
                <Command.Group heading="Runs" className="text-xs text-text-3 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5">
                  {runs.slice(0, 50).map((r) => (
                    <Command.Item key={r.id} value={`${r.task_key} run ${r.number} ${r.task_title}`} className={ITEM} onSelect={() => go(`/runs/${r.id}`)}>
                      <span className="mono w-14 shrink-0 text-text-3">{r.task_key}</span>
                      <span className="flex-1 truncate">
                        Run {r.number}: {r.task_title}
                      </span>
                      <StatusPill state={runState(r.status)} compact />
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
