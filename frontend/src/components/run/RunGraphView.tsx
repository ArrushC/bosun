import { Crosshair, Eye, Maximize2, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { ExecutionEvent, Run } from "@/api/types";
import { AgentGraph } from "@/components/graph/AgentGraph";
import { FlightRecorder } from "@/components/graph/FlightRecorder";
import { Inspector } from "@/components/graph/Inspector";
import { Button } from "@/components/ui/button";
import { EmptyState, Spinner } from "@/components/ui/display";
import { Segmented, Switch } from "@/components/ui/inputs";
import { DropdownCheckbox, DropdownContent, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger, Tip } from "@/components/ui/overlays";
import { DEFAULT_VIEW, type HiddenKind, viewGraph } from "@/lib/graph/reduce";
import { useRunModel } from "@/lib/graph/useRunModel";
import { useUi } from "@/stores/ui";

const HIDDEN_LABEL: Record<HiddenKind, string> = {
  tools: "Tool calls",
  files: "Files",
  messages: "Messages between agents",
  approvals: "Approvals",
  plan: "Plan and subtasks",
};

export function RunGraphView({ run, events, loading }: { run: Run; events: ExecutionEvent[]; loading: boolean }) {
  const graphPrefs = useUi((s) => s.graph);
  const setGraph = useUi((s) => s.setGraph);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);
  const [fitSignal, setFitSignal] = useState(0);

  useEffect(() => {
    setSelected(null);
    setFocus(null);
    setCursor(null);
  }, [run.id]);

  const live = useRunModel(events, null);
  const replay = useRunModel(events, cursor === null ? undefined : cursor);
  const full = live.model!;
  const model = replay.model ?? full;
  const version = cursor === null ? live.version : replay.version;
  const hidden = useMemo(() => new Set(graphPrefs.hidden), [graphPrefs.hidden]);
  const visible = useMemo(
    () => viewGraph(model, { ...DEFAULT_VIEW, aggregateTools: graphPrefs.aggregate, expanded, collapsed, focus, hidden }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, version, graphPrefs.aggregate, expanded, collapsed, focus, hidden],
  );

  const toggle = (set: Set<string>, id: string) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  };
  const onToggleExpand = useCallback((id: string) => setExpanded((s) => toggle(s, id)), []);
  const onToggleCollapse = useCallback((id: string) => setCollapsed((s) => toggle(s, id)), []);
  const selectedNode = selected ? (visible.nodes.find((n) => n.id === selected) ?? model.nodes.get(selected) ?? null) : null;

  const onSelectFromRecorder = (id: string) => {
    if (visible.nodes.some((n) => n.id === id)) {
      setSelected(id);
      return;
    }
    // Tool calls hidden in a group: select the agent that made them.
    const toolId = id.startsWith("tool:") ? id.slice(5) : null;
    const call = toolId ? model.tools.get(toolId) : undefined;
    setSelected(call ? call.agentId : model.nodes.has(id) ? id : null);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-2">
            <Segmented
              size="xs"
              value={graphPrefs.direction}
              onChange={(direction) => setGraph({ direction })}
              options={[
                { value: "LR", label: "Left to right" },
                { value: "TB", label: "Top down" },
              ]}
            />
            <DropdownMenu>
              <DropdownTrigger asChild>
                <Button size="xs" variant="secondary">
                  <SlidersHorizontal /> Show{graphPrefs.hidden.length ? ` (${5 - graphPrefs.hidden.length} of 5)` : ""}
                </Button>
              </DropdownTrigger>
              <DropdownContent align="start">
                <DropdownLabel>Layers</DropdownLabel>
                {(Object.keys(HIDDEN_LABEL) as HiddenKind[]).map((k) => (
                  <DropdownCheckbox
                    key={k}
                    checked={!hidden.has(k)}
                    onCheckedChange={(on) => setGraph({ hidden: on ? graphPrefs.hidden.filter((h) => h !== k) : [...graphPrefs.hidden, k] })}
                  >
                    {HIDDEN_LABEL[k]}
                  </DropdownCheckbox>
                ))}
                <DropdownSeparator />
                <DropdownCheckbox checked={graphPrefs.aggregate} onCheckedChange={(aggregate) => setGraph({ aggregate })}>
                  Group repeated tool calls
                </DropdownCheckbox>
                <DropdownCheckbox checked={graphPrefs.minimap} onCheckedChange={(minimap) => setGraph({ minimap })}>
                  Minimap
                </DropdownCheckbox>
              </DropdownContent>
            </DropdownMenu>
            {focus && (
              <span className="inline-flex h-8 items-center gap-2 rounded-full border border-accent/50 bg-panel pr-1.5 pl-3 text-xs text-text">
                <Crosshair className="size-3.5 text-accent" /> Focused on {model.nodes.get(focus)?.label ?? "node"}
                <button onClick={() => setFocus(null)} aria-label="Clear focus" className="rounded-full p-0.5 hover:bg-raised">
                  <X className="size-3.5" />
                </button>
              </span>
            )}
            {cursor !== null && (
              <span className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line bg-panel px-3 text-xs text-text-2">
                <Eye className="size-3.5" /> Replaying history
              </span>
            )}
          </div>
          <div className="absolute top-4 right-4 z-10 flex items-center gap-3 rounded-control border border-line-soft bg-panel/90 px-3 py-1.5">
            <label className="flex items-center gap-2 text-xs text-text-2">
              <Switch checked={follow} onCheckedChange={setFollow} label="Follow new activity" /> Follow
            </label>
            <Tip label="Fit to view">
              <button className="rounded p-0.5 text-text-2 hover:text-text" onClick={() => setFitSignal((n) => n + 1)} aria-label="Fit to view">
                <Maximize2 className="size-4" />
              </button>
            </Tip>
          </div>
          {loading && !events.length ? (
            <div className="flex h-full items-center justify-center">
              <Spinner />
            </div>
          ) : visible.nodes.length === 0 ? (
            <EmptyState title="Waiting for the first event" className="h-full">
              The graph fills in as Bosun records what the agents do.
            </EmptyState>
          ) : (
            <AgentGraph
              nodes={visible.nodes}
              edges={visible.edges}
              direction={graphPrefs.direction}
              selectedId={selected}
              focusId={focus}
              expanded={expanded}
              onSelect={setSelected}
              onToggleExpand={onToggleExpand}
              onToggleCollapse={onToggleCollapse}
              showMinimap={graphPrefs.minimap}
              follow={follow && cursor === null}
              fitSignal={fitSignal}
            />
          )}
        </div>
        {selectedNode && (
          <Inspector
            node={selectedNode}
            model={model}
            run={run}
            onClose={() => setSelected(null)}
            onFocus={(id) => setFocus((f) => (f === id ? null : id))}
          />
        )}
      </div>
      <FlightRecorder model={full} version={live.version} events={events} cursor={cursor} onCursor={setCursor} onSelect={onSelectFromRecorder} live={run.active} />
    </div>
  );
}
