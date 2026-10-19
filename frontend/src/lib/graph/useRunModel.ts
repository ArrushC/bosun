import { useMemo, useRef } from "react";

import type { ExecutionEvent } from "@/api/types";

import { createBuilder, type ModelBuilder, type RunModel } from "./reduce";

interface State {
  builder: ModelBuilder;
  applied: number;
  firstSeq: number;
  lastSeq: number;
}

/** Number of events with seq <= upTo (events are sorted by seq). */
function countUpTo(events: ExecutionEvent[], upTo: number): number {
  let lo = 0;
  let hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid].seq <= upTo) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Incrementally reduce a run's event log. When the log only grew (live events)
 * or the replay cursor moved forward, only the new events are applied; any other
 * change (scrubbing backwards, an out-of-order insert) rebuilds from scratch.
 * The model object is mutated in place, so consumers key memos on `version`.
 */
export function useRunModel(events: ExecutionEvent[], upTo: number | null | undefined): { model: RunModel | null; version: number } {
  const ref = useRef<State | null>(null);
  return useMemo(() => {
    if (upTo === undefined) return { model: null, version: -1 };
    const target = upTo === null ? events.length : countUpTo(events, upTo);
    let state = ref.current;
    const reusable =
      state !== null &&
      state.applied <= target &&
      (events[0]?.seq ?? 0) === state.firstSeq &&
      (state.applied === 0 || events[state.applied - 1]?.seq === state.lastSeq);
    if (!reusable || state === null) state = { builder: createBuilder(), applied: 0, firstSeq: events[0]?.seq ?? 0, lastSeq: 0 };
    for (let i = state.applied; i < target; i++) state.builder.apply(events[i]);
    state.applied = target;
    state.lastSeq = target ? events[target - 1].seq : 0;
    ref.current = state;
    return { model: state.builder.finalize(), version: target * 1_000_003 + (events[target - 1]?.seq ?? 0) };
  }, [events, upTo]);
}
