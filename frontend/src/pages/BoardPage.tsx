import {
  closestCorners,
  DndContext,
  type DragEndEvent,
  type DragOverEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { KanbanSquare, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useMoveTask, useTasks } from "@/api/hooks";
import type { Task, TaskStatus } from "@/api/types";
import { BoardToolbar } from "@/components/board/BoardToolbar";
import { columns, filterTasks, groupKey, neighbours } from "@/components/board/filter";
import { SortableTaskCard, TaskCardView } from "@/components/board/TaskCard";
import { useOpenTask } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorNote, Spinner } from "@/components/ui/display";
import { TASK_COLUMNS } from "@/lib/status";
import { cn } from "@/lib/utils";
import { useUi } from "@/stores/ui";

type Columns = Record<TaskStatus, string[]>;

function Column({ id, label, icon: Icon, ids, tasks, onOpen, onAdd }: {
  id: TaskStatus;
  label: string;
  icon: (typeof TASK_COLUMNS)[number]["icon"];
  ids: string[];
  tasks: Map<string, Task>;
  onOpen: (id: string) => void;
  onAdd: () => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `column:${id}`, data: { status: id } });
  const { groupBy } = useUi((s) => s.board);
  let lastGroup: string | null = null;
  return (
    <section aria-label={label} className="flex h-full w-[304px] shrink-0 flex-col">
      <header className="flex h-10 items-center gap-2.5 px-1.5">
        <Icon className={cn("size-4 text-text-3", id === "in_progress" && "text-st-run")} />
        <h2 className="text-sm font-semibold">{label}</h2>
        <span className="text-sm text-text-3">{ids.length}</span>
        <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label={`Add task to ${label}`} onClick={onAdd}>
          <Plus />
        </Button>
      </header>
      <div
        ref={setNodeRef}
        className={cn(
          "flex min-h-24 flex-1 flex-col gap-3 overflow-y-auto rounded-[16px] border border-transparent p-1 pb-8 transition-colors",
          isOver && "border-dashed border-line bg-panel/40",
        )}
      >
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {ids.map((taskId) => {
            const task = tasks.get(taskId);
            if (!task) return null;
            const group = groupBy !== "none" ? groupKey(task, groupBy) : null;
            const header = group !== null && group !== lastGroup;
            lastGroup = group;
            return (
              <div key={taskId}>
                {header && <p className="px-1.5 pt-3 pb-2 text-xs font-medium text-text-3 capitalize">{group}</p>}
                <SortableTaskCard task={task} onOpen={() => onOpen(taskId)} />
              </div>
            );
          })}
        </SortableContext>
        {ids.length === 0 && <p className="rounded-[14px] border border-dashed border-line-soft px-3 py-8 text-center text-sm text-text-3">Drop tasks here</p>}
      </div>
    </section>
  );
}

export function BoardPage() {
  const { data: tasks = [], isLoading, error } = useTasks();
  const board = useUi((s) => s.board);
  const setCreate = useUi((s) => s.setCreate);
  const openTask = useOpenTask();
  const move = useMoveTask();

  const visible = useMemo(() => filterTasks(tasks, board), [tasks, board]);
  const byId = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);
  const grouped = useMemo(() => {
    const cols = columns(visible, board.sortBy);
    if (board.groupBy !== "none") {
      for (const key of Object.keys(cols) as TaskStatus[]) {
        cols[key] = [...cols[key]].sort((a, b) => groupKey(a, board.groupBy).localeCompare(groupKey(b, board.groupBy)));
      }
    }
    return Object.fromEntries(Object.entries(cols).map(([k, v]) => [k, v.map((t) => t.id)])) as Columns;
  }, [visible, board.sortBy, board.groupBy]);

  const [items, setItems] = useState<Columns>(grouped);
  const [activeId, setActiveId] = useState<string | null>(null);
  // Keep the dropped layout until the move is saved, so cards never snap back.
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!activeId && !saving) setItems(grouped);
  }, [grouped, activeId, saving]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    // Space picks a card up (Enter opens it); arrows move it; Space/Enter drops it.
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
      keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space", "Enter"] },
    }),
  );
  const manual = board.sortBy === "manual" && board.groupBy === "none";

  const containerOf = (id: string): TaskStatus | null => {
    if (id.startsWith("column:")) return id.slice(7) as TaskStatus;
    return (Object.keys(items) as TaskStatus[]).find((k) => items[k].includes(id)) ?? null;
  };

  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id));

  const onDragOver = (e: DragOverEvent) => {
    const over = e.over?.id ? String(e.over.id) : null;
    if (!over) return;
    const from = containerOf(String(e.active.id));
    const to = containerOf(over);
    if (!from || !to || from === to) return;
    setItems((prev) => {
      const source = prev[from].filter((id) => id !== e.active.id);
      const target = [...prev[to]];
      const overIndex = target.indexOf(over);
      target.splice(overIndex >= 0 ? overIndex : target.length, 0, String(e.active.id));
      return { ...prev, [from]: source, [to]: target };
    });
  };

  const onDragEnd = (e: DragEndEvent) => {
    const id = String(e.active.id);
    setActiveId(null);
    const over = e.over?.id ? String(e.over.id) : null;
    const task = byId.get(id);
    if (!over || !task) {
      setItems(grouped);
      return;
    }
    const to = containerOf(over) ?? (task.status as TaskStatus);
    let column = items[to];
    if (manual && !over.startsWith("column:") && column.includes(over) && over !== id) {
      column = arrayMove(column, column.indexOf(id), column.indexOf(over));
      setItems((prev) => ({ ...prev, [to]: column }));
    }
    const changedColumn = to !== task.status;
    const unchangedOrder = grouped[to].join(",") === column.join(",");
    if (!changedColumn && (!manual || unchangedOrder)) return; // dropped where it was
    const { before_id, after_id } = manual ? neighbours(column, id) : { before_id: null, after_id: null };
    // Optimistic position between the new neighbours (the server computes the same).
    const after = after_id ? byId.get(after_id)?.position : undefined;
    const before = before_id ? byId.get(before_id)?.position : undefined;
    const position =
      after !== undefined && before !== undefined ? (after + before) / 2 : after !== undefined ? after + 1024 : before !== undefined ? before - 1024 : undefined;
    setSaving(true);
    move.mutate({ id, status: to, before_id, after_id, position }, { onSettled: () => setSaving(false) });
  };

  const active = activeId ? byId.get(activeId) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Board"
        actions={
          <Button variant="primary" onClick={() => setCreate(true)}>
            <Plus /> New task
          </Button>
        }
      >
        <BoardToolbar total={tasks.length} shown={visible.length} />
      </PageHeader>
      {isLoading && (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      )}
      {error && (
        <div className="px-8 py-4">
          <ErrorNote title="The board could not be loaded">{(error as Error).message}</ErrorNote>
        </div>
      )}
      {!isLoading && !error && tasks.length === 0 && (
        <EmptyState
          icon={KanbanSquare}
          title="No tasks yet"
          action={
            <div className="flex gap-2">
              <Button variant="primary" onClick={() => setCreate(true)}>
                <Plus /> New task
              </Button>
              <Button variant="secondary" asChild>
                <a href="/inbox">Review discovered work</a>
              </Button>
            </div>
          }
        >
          Create a task, or promote TODOs and GitHub issues from the Inbox.
        </EmptyState>
      )}
      {tasks.length > 0 && (
        <DndContext sensors={sensors} collisionDetection={closestCorners} onDragStart={onDragStart} onDragOver={onDragOver} onDragEnd={onDragEnd} onDragCancel={() => { setActiveId(null); setItems(grouped); }}>
          <div className="flex min-h-0 flex-1 gap-5 overflow-x-auto px-7 pt-1 pb-2">
            {TASK_COLUMNS.filter((c) => board.showDone || c.id !== "done").map((c) => (
              <Column
                key={c.id}
                id={c.id}
                label={c.label}
                icon={c.icon}
                ids={items[c.id]}
                tasks={byId}
                onOpen={openTask}
                onAdd={() => setCreate(true, c.id)}
              />
            ))}
          </div>
          <DragOverlay dropAnimation={{ duration: 160, easing: "cubic-bezier(.2,.8,.2,1)" }}>
            {active ? <TaskCardView task={active} overlay /> : null}
          </DragOverlay>
        </DndContext>
      )}
    </div>
  );
}
