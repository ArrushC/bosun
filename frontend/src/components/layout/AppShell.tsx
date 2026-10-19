import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Outlet, useSearchParams } from "react-router";

import { LiveConnection } from "@/api/live";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { Rail } from "@/components/layout/Rail";
import { TaskFormDialog } from "@/components/task/TaskFormDialog";
import { TaskSheet } from "@/components/task/TaskSheet";
import { useUi } from "@/stores/ui";

export function AppShell() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const taskId = params.get("task");
  const { createOpen, createStatus, setCreate, setPalette } = useUi();

  useEffect(() => {
    const live = new LiveConnection(qc);
    live.start();
    return () => live.stop();
  }, [qc]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      } else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && e.key === "c") {
        e.preventDefault();
        setCreate(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCreate, setPalette]);

  const closeTask = () => {
    const next = new URLSearchParams(params);
    next.delete("task");
    setParams(next, { replace: true });
  };

  return (
    <div className="flex h-full min-h-0">
      <Rail />
      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
      <TaskSheet taskId={taskId} onClose={closeTask} />
      <TaskFormDialog open={createOpen} onOpenChange={(open) => setCreate(open)} defaults={createStatus ? { status: createStatus } : undefined} />
      <CommandPalette />
    </div>
  );
}

export function useOpenTask() {
  const [params, setParams] = useSearchParams();
  return (taskId: string) => {
    const next = new URLSearchParams(params);
    next.set("task", taskId);
    setParams(next);
  };
}
