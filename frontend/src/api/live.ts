import type { QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import { useEvents } from "@/stores/events";

import { API } from "./client";
import type { ExecutionEvent } from "./types";
import { keys } from "./hooks";

export type LiveStatus = "connecting" | "open" | "reconnecting" | "offline";

export const useLiveStatus = create<{ status: LiveStatus; lastSeq: number; set: (s: Partial<{ status: LiveStatus; lastSeq: number }>) => void }>(
  (set) => ({ status: "connecting", lastSeq: 0, set: (s) => set(s) }),
);

type EntityMessage = { kind: "entity"; entity: string; id: string | null; action: string };
type Message =
  | { kind: "hello"; last_seq: number }
  | { kind: "event"; event: ExecutionEvent; replay?: boolean }
  | EntityMessage
  | { kind: "replay_done"; run_id: string; last_seq: number }
  | { kind: "resync" }
  | { kind: "ping" }
  | { kind: "pong" };

type Key = readonly unknown[];

const STALE_MS = 45_000;

const ENTITY_KEYS: Record<string, (id: string | null) => Key[]> = {
  task: (id) => [keys.tasks, keys.dashboard, keys.overview, ...(id ? [keys.task(id)] : [])],
  run: (id) => [["runs"], keys.dashboard, keys.tasks, keys.overview, ...(id ? [keys.run(id), keys.diff(id)] : [])],
  approval: () => [keys.approvals, keys.dashboard, ["run"]],
  workspace: () => [keys.workspaces, keys.dashboard],
  sources: () => [["records"], keys.dashboard],
  connection: () => [keys.connections],
  roles: () => [keys.roles],
  settings: () => [keys.settings, keys.provider],
};

/**
 * One WebSocket per tab. Persisted events stream into the event store (batched
 * per animation frame); entity notifications invalidate cached queries. After a
 * reconnect, every loaded run replays from its last seen `seq` and all queries
 * refetch, so nothing is lost while disconnected.
 */
export class LiveConnection {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private everConnected = false;
  private closed = false;
  private buffer: ExecutionEvent[] = [];
  private flushScheduled = false;
  private pendingInvalidations = new Map<string, Key>();
  private invalidateTimer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private readonly onWake = () => {
    if (this.closed) return;
    if (!this.ws || this.ws.readyState > WebSocket.OPEN || Date.now() - this.lastMessageAt > STALE_MS) this.reconnectNow();
  };

  constructor(private readonly qc: QueryClient) {}

  start() {
    this.closed = false;
    this.open();
    // The server pings every 20 s; silence for longer means a half-open socket.
    this.watchdog = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN && Date.now() - this.lastMessageAt > STALE_MS) this.reconnectNow();
    }, 10_000);
    window.addEventListener("online", this.onWake);
    document.addEventListener("visibilitychange", this.onWake);
  }

  stop() {
    this.closed = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.watchdog) clearInterval(this.watchdog);
    window.removeEventListener("online", this.onWake);
    document.removeEventListener("visibilitychange", this.onWake);
    this.ws?.close();
  }

  private reconnectNow() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    const old = this.ws;
    this.ws = null;
    if (old) {
      old.onclose = null;
      old.close();
    }
    this.open();
  }

  private url() {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${window.location.host}${API}/ws`;
  }

  private open() {
    useLiveStatus.getState().set({ status: this.everConnected ? "reconnecting" : "connecting" });
    const ws = new WebSocket(this.url());
    this.ws = ws;
    ws.onopen = () => {
      this.lastMessageAt = Date.now();
      ws.send(JSON.stringify({ type: "subscribe", topics: ["entities", "runs"] }));
    };
    ws.onmessage = (msg) => {
      this.lastMessageAt = Date.now();
      let parsed: Message;
      try {
        parsed = JSON.parse(msg.data as string) as Message;
      } catch {
        return;
      }
      this.handle(parsed);
    };
    ws.onclose = () => {
      if (this.closed) return;
      useLiveStatus.getState().set({ status: this.attempt > 3 ? "offline" : "reconnecting" });
      const delay = Math.min(10_000, 500 * 2 ** this.attempt);
      this.attempt += 1;
      this.retryTimer = setTimeout(() => this.open(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private handle(msg: Message) {
    switch (msg.kind) {
      case "hello": {
        this.attempt = 0;
        const reconnected = this.everConnected;
        this.everConnected = true;
        useLiveStatus.getState().set({ status: "open", lastSeq: msg.last_seq });
        if (reconnected) this.catchUp();
        break;
      }
      case "event":
        this.buffer.push(msg.event);
        this.scheduleFlush();
        break;
      case "entity":
        for (const key of ENTITY_KEYS[msg.entity]?.(msg.id) ?? []) this.queueInvalidate(key);
        break;
      case "resync":
        this.resync();
        break;
      default:
        break;
    }
  }

  private catchUp() {
    void this.qc.invalidateQueries();
    const { runs } = useEvents.getState();
    for (const [runId, log] of Object.entries(runs)) {
      if (log.loaded) this.ws?.send(JSON.stringify({ type: "replay", run_id: runId, after_seq: log.lastSeq }));
    }
  }

  private resync() {
    void this.qc.invalidateQueries();
    const store = useEvents.getState();
    for (const runId of store.loadedRunIds()) void store.load(runId, true);
  }

  private scheduleFlush() {
    if (this.flushScheduled) return;
    this.flushScheduled = true;
    const flush = () => {
      this.flushScheduled = false;
      const batch = this.buffer;
      this.buffer = [];
      useEvents.getState().ingest(batch);
    };
    if (typeof requestAnimationFrame === "function" && document.visibilityState === "visible") requestAnimationFrame(flush);
    else setTimeout(flush, 100);
  }

  private queueInvalidate(key: Key) {
    this.pendingInvalidations.set(JSON.stringify(key), key);
    if (this.invalidateTimer) return;
    this.invalidateTimer = setTimeout(() => {
      this.invalidateTimer = null;
      const batch = [...this.pendingInvalidations.values()];
      this.pendingInvalidations.clear();
      for (const queryKey of batch) void this.qc.invalidateQueries({ queryKey });
    }, 150);
  }
}
