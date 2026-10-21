import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { useSaveWorkflow, useWorkflow } from "@/api/hooks";
import type { Workflow, WorkflowIssue } from "@/api/types";
import { normalizeStep, type Step } from "@/lib/workflow";

export interface Draft {
  name: string;
  description: string;
  steps: Step[];
}

/** saved: nothing to save · saving: a save is pending or in flight · retrying: the last save failed · conflict: saved elsewhere first */
export type SaveState = "saved" | "saving" | "retrying" | "conflict";

const SAVE_DELAY_MS = 300;
const RETRY_DELAYS_MS = [2000, 5000, 10_000, 20_000];
const HISTORY_LIMIT = 100;

function fromServer(w: Workflow): Draft {
  return { name: w.name, description: w.description, steps: (w.spec.steps ?? []).map(normalizeStep) };
}

/**
 * The editor's working copy of a workflow. Edits apply locally at once and are
 * saved shortly after, naming the revision they were based on; a save that
 * loses to another tab becomes a conflict for the person to resolve rather than
 * an overwrite. The server's copy is adopted on load, and later only when there
 * is nothing unsaved here.
 */
export function useWorkflowDraft(id: string) {
  const query = useWorkflow(id);
  const save = useSaveWorkflow(id);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issues, setIssues] = useState<WorkflowIssue[]>([]);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [lastError, setLastError] = useState<string | null>(null);
  const [historySize, setHistorySize] = useState({ past: 0, future: 0 });

  const latest = useRef<Draft | null>(null);
  const revision = useRef(0);
  const edits = useRef(0); // local edits so far
  const saved = useRef(0); // local edits the server has
  const inFlight = useRef(false);
  const state = useRef<SaveState>("saved");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const failures = useRef(0);
  const history = useRef<{ past: Draft[]; future: Draft[] }>({ past: [], future: [] });
  const checkpointed = useRef<Draft | null>(null); // the state before a text edit, recorded once it changes
  const flushRef = useRef<() => Promise<void>>(async () => {});

  const setState = (next: SaveState) => {
    state.current = next;
    setSaveState(next);
  };

  const adopt = useCallback((w: Workflow) => {
    const next = fromServer(w);
    latest.current = next;
    revision.current = w.revision;
    edits.current = saved.current = 0;
    history.current = { past: [], future: [] };
    checkpointed.current = null;
    failures.current = 0;
    setHistorySize({ past: 0, future: 0 });
    setDraft(next);
    setIssues(w.issues);
    setLastError(null);
    setState("saved");
  }, []);

  useEffect(() => {
    const data = query.data;
    if (!data) return;
    const idle = edits.current === saved.current && !inFlight.current && state.current === "saved";
    if (!latest.current || (idle && data.revision > revision.current)) adopt(data);
  }, [query.data, adopt]);

  const schedule = useCallback((ms: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flushRef.current(), ms);
  }, []);

  flushRef.current = async () => {
    if (inFlight.current || state.current === "conflict" || !latest.current) return;
    if (edits.current === saved.current) {
      setState("saved");
      return;
    }
    const version = edits.current;
    const d = latest.current;
    inFlight.current = true;
    setState("saving");
    try {
      const result = await save.mutateAsync({
        revision: revision.current,
        name: d.name.trim() || undefined,
        description: d.description,
        spec: { steps: d.steps },
      });
      revision.current = result.revision;
      saved.current = version;
      failures.current = 0;
      setIssues(result.issues);
      setLastError(null);
      setState(edits.current === version ? "saved" : "saving");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setState("conflict");
      } else {
        setLastError(e instanceof Error ? e.message : String(e));
        setState("retrying");
        schedule(RETRY_DELAYS_MS[Math.min(failures.current++, RETRY_DELAYS_MS.length - 1)]);
      }
    } finally {
      inFlight.current = false;
      if (state.current === "saving" && edits.current !== saved.current) schedule(150);
    }
  };

  const commit = useCallback(
    (next: Draft, recordHistory: boolean) => {
      const prev = latest.current;
      if (!prev || next === prev) return;
      const before = recordHistory ? prev : checkpointed.current;
      checkpointed.current = null;
      if (before) {
        history.current.past = [...history.current.past, before].slice(-HISTORY_LIMIT);
        history.current.future = [];
        setHistorySize({ past: history.current.past.length, future: 0 });
      }
      latest.current = next;
      edits.current += 1;
      setDraft(next);
      if (state.current !== "conflict") {
        setState("saving");
        schedule(SAVE_DELAY_MS);
      }
    },
    [schedule],
  );

  /** Apply an edit. Text typing can skip history so each keystroke isn't an undo step. */
  const change = useCallback(
    (update: (d: Draft) => Draft, opts: { history?: boolean } = {}) => {
      if (!latest.current) return;
      commit(update(latest.current), opts.history ?? true);
    },
    [commit],
  );

  /** Start an undoable text edit: the state now becomes an undo step at the first keystroke. */
  const checkpoint = useCallback(() => {
    checkpointed.current = latest.current;
  }, []);

  const undo = useCallback(() => {
    checkpointed.current = null;
    const prev = history.current.past.pop();
    if (!prev || !latest.current) return;
    history.current.future.push(latest.current);
    setHistorySize({ past: history.current.past.length, future: history.current.future.length });
    commit(prev, false);
  }, [commit]);

  const redo = useCallback(() => {
    checkpointed.current = null;
    const next = history.current.future.pop();
    if (!next || !latest.current) return;
    history.current.past.push(latest.current);
    setHistorySize({ past: history.current.past.length, future: history.current.future.length });
    commit(next, false);
  }, [commit]);

  /** Conflict: drop local edits and load what was saved elsewhere. */
  const reload = useCallback(async () => {
    const fresh = await query.refetch();
    if (fresh.data) adopt(fresh.data);
  }, [query, adopt]);

  /** Conflict: keep local edits, saving them over the other copy. */
  const overwrite = useCallback(async () => {
    const fresh = await query.refetch();
    if (!fresh.data) return;
    revision.current = fresh.data.revision;
    setState("saving");
    await flushRef.current();
  }, [query]);

  const retryNow = useCallback(() => {
    failures.current = 0;
    schedule(0);
  }, [schedule]);

  const unsaved = saveState !== "saved";
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  return {
    query,
    draft,
    issues,
    saveState,
    lastError,
    revision: revision.current,
    change,
    checkpoint,
    undo,
    redo,
    canUndo: historySize.past > 0,
    canRedo: historySize.future > 0,
    reload,
    overwrite,
    retryNow,
  };
}
