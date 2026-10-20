import { ChevronDown, ChevronUp, Pause, Play, Radio, SkipBack } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ExecutionEvent } from "@/api/types";
import { Segmented } from "@/components/ui/inputs";
import { Tip } from "@/components/ui/overlays";
import type { Lane, Mark, RunModel, Span } from "@/lib/graph/reduce";
import { STATE } from "@/lib/status";
import { clockTime, cn, formatDuration } from "@/lib/utils";

const MARK_STYLE: Record<Mark["kind"], { color: string; shape: "tick" | "diamond" | "dot" | "x" | "flag" }> = {
  text: { color: "var(--text-3)", shape: "tick" },
  user: { color: "var(--accent)", shape: "diamond" },
  "message-in": { color: "var(--accent)", shape: "dot" },
  "message-out": { color: "var(--accent)", shape: "dot" },
  denied: { color: "var(--st-fail)", shape: "x" },
  file: { color: "var(--st-ok)", shape: "dot" },
  result: { color: "var(--text-2)", shape: "flag" },
};

function spanColor(span: Span) {
  if (span.kind === "approval") return "var(--st-approval)";
  if (span.state === "failed") return "var(--st-fail)";
  if (span.state === "running") return "var(--st-run)";
  if (span.state === "waiting_approval") return "var(--st-approval)";
  return "var(--text-3)";
}

export interface RecorderProps {
  model: RunModel;
  version: number;
  events: ExecutionEvent[];
  cursor: number | null; // null = live
  onCursor: (seq: number | null) => void;
  onSelect: (nodeId: string) => void;
  live: boolean;
}

export function FlightRecorder({ model, version, events, cursor, onCursor, onSelect, live }: RecorderProps) {
  const [open, setOpen] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<"1" | "4" | "16">("4");
  const [now, setNow] = useState(() => Date.now());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);

  const start = model.firstTs ? new Date(model.firstTs).getTime() : now;
  const end = cursor === null && live ? Math.max(now, model.lastTs ? new Date(model.lastTs).getTime() : now) : model.lastTs ? new Date(model.lastTs).getTime() : now;
  const total = Math.max(1000, end - start);
  const x = (iso: string | undefined) => (iso ? Math.min(100, Math.max(0, ((new Date(iso).getTime() - start) / total) * 100)) : 100);

  const index = cursor === null ? events.length - 1 : events.findIndex((e) => e.seq >= cursor);
  const cursorEvent = events[Math.max(0, index)];

  // Replay: advance one event at a time, pacing by real gaps compressed by speed.
  useEffect(() => {
    if (!playing) return;
    const i = cursor === null ? events.length - 1 : events.findIndex((e) => e.seq >= cursor);
    if (i < 0 || i >= events.length - 1) {
      // Reached the end of the recording: hand back to the live view.
      setPlaying(false);
      onCursor(null);
      return;
    }
    const gap = new Date(events[i + 1].ts).getTime() - new Date(events[i].ts).getTime();
    const delay = Math.min(1200, Math.max(40, gap / Number(speed)));
    timer.current = setTimeout(() => onCursor(events[i + 1].seq), delay);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [playing, cursor, events, speed, onCursor]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const lanes = useMemo(() => [...model.lanes.values()], [model, version]);
  const cursorX = cursor === null ? null : x(cursorEvent?.ts);
  const elapsed = model.firstTs ? (cursor === null ? (live ? now : end) - start : new Date(cursorEvent?.ts ?? model.firstTs).getTime() - start) : 0;

  return (
    <section aria-label="Flight recorder" className="shrink-0 border-t border-line-soft bg-panel">
      <div className="flex h-10 items-center gap-2 px-3">
        <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1.5 text-sm font-semibold text-text" aria-expanded={open}>
          {open ? <ChevronDown className="size-4 text-text-3" /> : <ChevronUp className="size-4 text-text-3" />}
          Flight recorder
        </button>
        <span className="text-xs text-text-3">
          {lanes.length} agent{lanes.length === 1 ? "" : "s"}, {model.eventCount} events
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Tip label="Replay from the start">
            <button
              className="rounded-control p-1.5 text-text-2 hover:bg-raised hover:text-text"
              aria-label="Replay from start"
              onClick={() => {
                if (!events.length) return;
                onCursor(events[0].seq);
                setPlaying(true);
              }}
            >
              <SkipBack className="size-3.5" />
            </button>
          </Tip>
          <Tip label={playing ? "Pause replay" : "Play"}>
            <button
              className="rounded-control p-1.5 text-text-2 hover:bg-raised hover:text-text disabled:opacity-40"
              aria-label={playing ? "Pause replay" : "Play replay"}
              disabled={!events.length}
              onClick={() => {
                if (!playing && (cursor === null || index >= events.length - 1)) onCursor(events[0]?.seq ?? null);
                setPlaying((v) => !v);
              }}
            >
              {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
            </button>
          </Tip>
          <Segmented
            size="xs"
            value={speed}
            onChange={setSpeed}
            options={[
              { value: "1", label: "1×" },
              { value: "4", label: "4×" },
              { value: "16", label: "16×" },
            ]}
          />
          <input
            type="range"
            aria-label="Scrub through events"
            min={0}
            max={Math.max(0, events.length - 1)}
            value={Math.max(0, index)}
            onChange={(e) => {
              setPlaying(false);
              const i = Number(e.target.value);
              onCursor(i >= events.length - 1 ? null : events[i].seq);
            }}
            className="w-44 accent-[var(--accent)]"
          />
          <span className="mono w-24 text-right text-2xs text-text-3">{formatDuration(elapsed)}</span>
          <button
            onClick={() => {
              setPlaying(false);
              onCursor(null);
            }}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-full border px-2 text-2xs font-semibold",
              cursor === null ? "border-st-run/50 text-st-run" : "border-line text-text-2 hover:text-text",
            )}
            aria-pressed={cursor === null}
          >
            <Radio className="size-3" /> {cursor === null ? (live ? "Live" : "Latest") : "Back to latest"}
          </button>
        </div>
      </div>

      {open && (
        <div className="relative max-h-[230px] overflow-y-auto border-t border-line-soft pb-2">
          {lanes.length === 0 && <p className="px-4 py-5 text-sm text-text-3">Agent activity will appear here as soon as the first session starts.</p>}
          {lanes.map((lane) => (
            <LaneRow key={lane.id} lane={lane} x={x} onSelect={onSelect} />
          ))}
          {cursorX !== null && (
            <div className="pointer-events-none absolute top-0 bottom-0 left-[176px] right-3">
              <div className="absolute top-0 bottom-0 w-px bg-accent" style={{ left: `${cursorX}%` }}>
                <span className="absolute -top-0 left-1 rounded-[3px] bg-accent px-1 text-2xs font-semibold text-accent-fg">
                  {cursorEvent ? clockTime(cursorEvent.ts) : ""}
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function LaneRow({ lane, x, onSelect }: { lane: Lane; x: (iso?: string) => number; onSelect: (id: string) => void }) {
  const state = STATE[lane.state];
  return (
    <div className="flex h-7 items-center">
      <button
        onClick={() => onSelect(lane.id)}
        className={cn("flex w-[176px] shrink-0 items-center gap-2 truncate px-3 text-left text-xs text-text-2 hover:text-text", lane.kind === "subagent" && "pl-6")}
        title={lane.label}
      >
        <span className="h-3 w-[3px] shrink-0 rounded-full" style={{ background: lane.color }} />
        <span className="truncate">{lane.label}</span>
        <span className="ml-auto size-1.5 shrink-0 rounded-full" style={{ background: state.color }} title={state.label} />
      </button>
      <div className="relative mr-3 h-5 flex-1 rounded-[3px] bg-sunken">
        <div
          className="absolute top-0 bottom-0 rounded-[3px]"
          style={{ left: `${x(lane.start)}%`, width: `${Math.max(0.4, x(lane.end) - x(lane.start))}%`, background: `color-mix(in srgb, ${lane.color} 10%, transparent)` }}
        />
        {lane.spans
          .filter((s) => s.kind !== "session")
          .map((span) => {
            const left = x(span.start);
            const width = Math.max(0.35, x(span.end) - left);
            return (
              <Tip key={span.id} label={<span><b>{span.label}</b><br />{STATE[span.state].label}{span.end ? `, ${formatDuration(new Date(span.end).getTime() - new Date(span.start).getTime())}` : ""}</span>}>
                <button
                  onClick={() => onSelect(span.kind === "approval" ? span.id : `tool:${span.id}`)}
                  aria-label={span.label}
                  className={cn(
                    "absolute top-1/2 h-2 -translate-y-1/2 rounded-[2px]",
                    span.kind === "approval" && "h-3 border border-st-approval bg-transparent",
                    span.state === "running" && "animate-pulse",
                  )}
                  style={{ left: `${left}%`, width: `${width}%`, minWidth: 3, background: span.kind === "approval" ? undefined : spanColor(span) }}
                />
              </Tip>
            );
          })}
        {lane.marks.map((m, i) => {
          const style = MARK_STYLE[m.kind];
          return (
            <Tip key={`${m.seq}-${i}`} label={m.label.slice(0, 220)}>
              <span
                className={cn(
                  "absolute top-1/2 -translate-x-1/2 -translate-y-1/2",
                  style.shape === "tick" && "h-3 w-px",
                  style.shape === "dot" && "size-1.5 rounded-full",
                  style.shape === "diamond" && "size-2 rotate-45",
                  style.shape === "x" && "size-2 rotate-45 rounded-[1px]",
                  style.shape === "flag" && "h-4 w-[2px]",
                )}
                style={{ left: `${x(m.ts)}%`, background: style.color }}
              />
            </Tip>
          );
        })}
      </div>
    </div>
  );
}
