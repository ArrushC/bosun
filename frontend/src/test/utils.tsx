import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";

import { TooltipProvider } from "@/components/ui/overlays";

export interface Call {
  method: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
}

/** Route-based fetch mock. Handlers return JSON bodies (or [status, body]). */
export function mockApi(routes: Record<string, (call: Call) => unknown>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(Object.entries((init?.headers as Record<string, string>) ?? {}));
    const call = { method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers };
    calls.push(call);
    const path = url.replace(/^\/api\/v1/, "").split("?")[0];
    const handler = routes[`${method} ${path}`] ?? routes[path];
    if (!handler) return new Response(JSON.stringify({ detail: `unmocked ${method} ${path}` }), { status: 404 });
    const result = await handler(call);
    const [status, body] = Array.isArray(result) && typeof result[0] === "number" ? (result as [number, unknown]) : [200, result];
    return new Response(body === undefined ? null : JSON.stringify(body), { status: body === undefined ? 204 : status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function renderWithProviders(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

export const baseTask = {
  id: "tsk_1",
  key: "BOS-1",
  number: 1,
  title: "Fix the crash on empty cart",
  description: "Steps to reproduce…",
  status: "ready",
  priority: "high",
  kind: "bugfix",
  labels: ["bug"],
  workspace_id: "ws_1",
  workspace_name: "shop",
  acceptance_criteria: "",
  agent_instructions: "",
  execution_mode: "single",
  attachments: [],
  position: 0,
  source_state: null,
  last_activity_at: null,
  last_activity: null,
  created_at: "2026-10-19T09:00:00Z",
  updated_at: "2026-10-19T09:00:00Z",
  completed_at: null,
  archived: false,
  is_demo: false,
  sources: [],
  depends_on: [],
  open_dependencies: 0,
  latest_run: null,
};
