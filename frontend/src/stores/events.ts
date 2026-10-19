import { create } from "zustand";

import { api } from "@/api/client";
import type { ExecutionEvent } from "@/api/types";

/**
 * Per-run execution event logs, ordered by `seq` and de-duplicated.
 *
 * A run's log is "loaded" once its durable history has been fetched; live
 * events that arrive while history is loading are held and merged afterwards,
 * so the reconstructed graph never misses or double-counts an event.
 */
export interface RunLog {
  events: ExecutionEvent[];
  lastSeq: number;
  loaded: boolean;
  loading: boolean;
  error?: string;
}

interface EventsState {
  runs: Record<string, RunLog>;
  pending: Record<string, ExecutionEvent[]>;
  ingest: (events: ExecutionEvent[]) => void;
  load: (runId: string, force?: boolean) => Promise<void>;
  reset: (runId: string) => void;
  loadedRunIds: () => string[];
}

export function mergeEvents(existing: ExecutionEvent[], incoming: ExecutionEvent[]): ExecutionEvent[] {
  if (!incoming.length) return existing;
  const last = existing.length ? existing[existing.length - 1].seq : 0;
  const sortedIncoming = [...incoming].sort((a, b) => a.seq - b.seq);
  // Fast path: strictly newer events appended in order.
  if (sortedIncoming[0].seq > last) {
    const out = existing.slice();
    let prev = last;
    for (const e of sortedIncoming) {
      if (e.seq > prev) {
        out.push(e);
        prev = e.seq;
      }
    }
    return out;
  }
  const bySeq = new Map<number, ExecutionEvent>();
  for (const e of existing) bySeq.set(e.seq, e);
  for (const e of sortedIncoming) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

const EMPTY: RunLog = { events: [], lastSeq: 0, loaded: false, loading: false };
/** Event logs kept in memory; the least recently opened runs are evicted. */
const MAX_LOGS = 6;
const recent: string[] = [];

function touch(runId: string): string[] {
  const i = recent.indexOf(runId);
  if (i >= 0) recent.splice(i, 1);
  recent.push(runId);
  return recent.length > MAX_LOGS ? recent.splice(0, recent.length - MAX_LOGS) : [];
}

export const useEvents = create<EventsState>((set, get) => ({
  runs: {},
  pending: {},

  ingest: (events) => {
    if (!events.length) return;
    const byRun = new Map<string, ExecutionEvent[]>();
    for (const e of events) {
      const list = byRun.get(e.run_id) ?? [];
      list.push(e);
      byRun.set(e.run_id, list);
    }
    set((state) => {
      const runs = { ...state.runs };
      const pending = { ...state.pending };
      for (const [runId, list] of byRun) {
        const log = runs[runId];
        if (!log) continue; // nobody is looking at this run
        if (!log.loaded) {
          pending[runId] = [...(pending[runId] ?? []), ...list];
          continue;
        }
        const merged = mergeEvents(log.events, list);
        runs[runId] = { ...log, events: merged, lastSeq: merged.length ? merged[merged.length - 1].seq : log.lastSeq };
      }
      return { runs, pending };
    });
  },

  load: async (runId, force = false) => {
    const current = get().runs[runId];
    const evicted = touch(runId);
    if (evicted.length) {
      set((s) => {
        const runs = { ...s.runs };
        const pending = { ...s.pending };
        for (const id of evicted) {
          delete runs[id];
          delete pending[id];
        }
        return { runs, pending };
      });
    }
    if (current && (current.loading || (current.loaded && !force))) return;
    // While (re)loading, live events are held in `pending` and merged when the
    // history arrives — also on a forced reload, so nothing is dropped.
    set((s) => ({ runs: { ...s.runs, [runId]: { ...(current ?? EMPTY), loaded: false, loading: true, error: undefined } } }));
    try {
      let after = 0;
      let all: ExecutionEvent[] = [];
      for (;;) {
        const page = await api.get<{ events: ExecutionEvent[]; last_seq: number; has_more: boolean }>(
          `/runs/${runId}/events?after_seq=${after}&limit=5000`,
        );
        all = mergeEvents(all, page.events);
        after = page.last_seq;
        if (!page.has_more) break;
      }
      set((s) => {
        const held = s.pending[runId] ?? [];
        const merged = mergeEvents(all, held);
        const pending = { ...s.pending };
        delete pending[runId];
        return {
          pending,
          runs: {
            ...s.runs,
            [runId]: { events: merged, lastSeq: merged.length ? merged[merged.length - 1].seq : 0, loaded: true, loading: false },
          },
        };
      });
    } catch (error) {
      set((s) => ({
        pending: Object.fromEntries(Object.entries(s.pending).filter(([id]) => id !== runId)),
        runs: {
          ...s.runs,
          [runId]: { ...(s.runs[runId] ?? EMPTY), loading: false, error: error instanceof Error ? error.message : "Failed to load events" },
        },
      }));
    }
  },

  reset: (runId) =>
    set((s) => {
      const runs = { ...s.runs };
      delete runs[runId];
      return { runs };
    }),

  loadedRunIds: () => Object.entries(get().runs).filter(([, log]) => log.loaded).map(([id]) => id),
}));

export function useRunLog(runId: string | undefined): RunLog {
  return useEvents((s) => (runId ? s.runs[runId] : undefined)) ?? EMPTY;
}
