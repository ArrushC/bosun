import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  type Connection,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeChange,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  useStore,
  useViewport,
} from "@xyflow/react";
import { Bot, ChevronDown, Flag, KanbanSquare, Maximize2, Minus, PanelRight, PenLine, Plus, Wand2 } from "lucide-react";
import { memo, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { Role, WorkflowIssue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { DropdownSeparator, Tip } from "@/components/ui/overlays";
import { STATE, type VisualState } from "@/lib/status";
import { cn } from "@/lib/utils";
import { connect, disconnect, removeSteps, type Step, STEP_SIZE, stepTitle, tidy } from "@/lib/workflow";

import { AgentMenu, type AgentInfo, agentColor, APPROVAL_COLOR, ApprovalIcon, IssueIcon } from "./parts";

/** A step's state in a run, shown on its node with an icon and a word. */
export interface StepStatus {
  state: VisualState;
  label: string;
}

interface StepNodeData extends Record<string, unknown> {
  step: Step;
  agent: AgentInfo | undefined;
  issue?: WorkflowIssue["level"];
  agents: Role[];
  /** The only selected step, so it offers to add a step after it. */
  solo: boolean;
  readOnly: boolean;
  status?: StepStatus;
  onAddAfter: (after: string, agent: Role) => void;
  onAddApprovalAfter?: (after: string) => void;
}

const HANDLE =
  "!size-3 !border-2 !border-accent !bg-panel opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 group-[.selected]:opacity-100";

const StepNode = memo(function StepNode({ data, selected }: NodeProps) {
  const { step, agent, issue, agents, solo, readOnly, status, onAddAfter, onAddApprovalAfter } = data as StepNodeData;
  const approval = step.kind === "approval";
  const color = approval ? APPROVAL_COLOR : agentColor(agent);
  const Icon = approval ? ApprovalIcon : Bot;
  const meta = status ? STATE[status.state] : null;
  return (
    <div
      className={cn(
        "group relative flex h-full w-full rounded-[12px] border bg-panel shadow-[0_1px_2px_rgb(0_0_0/0.06)] transition-colors",
        selected ? "selected border-accent ring-2 ring-accent/35" : issue === "error" ? "border-st-fail/70" : issue === "warning" ? "border-st-approval/70" : "border-line-soft hover:border-line",
      )}
    >
      <Handle type="target" position={Position.Top} className={HANDLE} isConnectable={!readOnly} />
      <span className="w-1 shrink-0 rounded-l-[11px]" style={{ background: color }} />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1 px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <Icon className="size-4 shrink-0" style={{ color }} />
          <span className="truncate text-sm font-semibold text-text">{step.title.trim() || "Untitled step"}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-2">
          <span className={cn("truncate", !approval && !agent && "text-st-fail")}>
            {approval ? (status ? "Approval" : "Approval by you") : agent ? agent.name : step.agent ? `${step.agent} (not available)` : "Choose an agent"}
          </span>
          {meta && status ? (
            <span className="ml-auto inline-flex shrink-0 items-center gap-1 text-2xs font-medium" style={{ color: meta.color }}>
              <meta.icon className={cn("size-3.5", meta.spin && "animate-spin motion-reduce:animate-none")} /> {status.label}
            </span>
          ) : (
            step.changes_code && (
              <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-[6px] bg-raised px-1.5 text-2xs text-text-2">
                <PenLine className="size-3" /> Edits code
              </span>
            )
          )}
        </div>
      </div>
      {issue && (
        <span
          className={cn(
            "absolute -top-2.5 -right-2.5 inline-flex h-5 items-center gap-1 rounded-full border bg-panel px-1.5 text-2xs font-medium",
            issue === "error" ? "border-st-fail/50 text-st-fail" : "border-st-approval/50 text-st-approval",
          )}
        >
          <IssueIcon level={issue} className="size-3" />
          {issue === "error" ? "Fix" : "Check"}
        </span>
      )}
      {selected && solo && !readOnly && (
        // The menu renders in a portal, but React bubbles its clicks and keys through this node,
        // where React Flow would take them as selecting the node again.
        <div className="contents" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <AgentMenu agents={agents} onPick={(a) => onAddAfter(step.key, a)} onPickApproval={onAddApprovalAfter && (() => onAddApprovalAfter(step.key))} align="center">
            <button
              className="nodrag absolute -bottom-4 left-1/2 z-10 flex size-8 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-panel text-text-2 shadow-sm hover:border-accent hover:text-accent"
              aria-label={`Add a step after ${step.title || "this step"}`}
            >
              <Plus className="size-4" />
            </button>
          </AgentMenu>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className={HANDLE} isConnectable={!readOnly} />
    </div>
  );
});

const AnchorNode = memo(function AnchorNode({ data }: NodeProps) {
  const { kind } = data as { kind: "task" | "result" };
  return (
    <div className="flex h-full w-full items-center justify-center gap-2 rounded-full border border-dashed border-line bg-bg px-4 text-sm text-text-2">
      {kind === "result" && <Handle type="target" position={Position.Top} className="!size-1.5 !border-0 !bg-line" isConnectable={false} />}
      {kind === "task" ? <KanbanSquare className="size-4" /> : <Flag className="size-4" />}
      {kind === "task" ? "Board task" : "Result for review"}
      {kind === "task" && <Handle type="source" position={Position.Bottom} className="!size-1.5 !border-0 !bg-line" isConnectable={false} />}
    </div>
  );
});

const nodeTypes = { step: StepNode, anchor: AnchorNode };
const ARROW = { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "var(--text-3)" };

export interface WorkflowCanvasProps {
  steps: Step[];
  agents: Role[];
  /** Every agent a step may name: today's, or those frozen into a version or run. */
  allRoles: AgentInfo[];
  issues: WorkflowIssue[];
  selected: string | null;
  onSelect: (key: string | null) => void;
  onChange: (update: (steps: Step[]) => Step[], label?: string) => void;
  onAdd: (agent: Role, after?: string | null) => void;
  onAddApproval?: (after?: string | null) => void;
  addMenuOpen: boolean;
  onAddMenuOpenChange: (open: boolean) => void;
  /** A published version or a run: steps can be selected, not changed. */
  readOnly?: boolean;
  /** Each step's state in a run. */
  status?: Map<string, StepStatus>;
  panelHidden?: boolean;
  /** Width covered by the settings panel when it floats over the canvas. */
  coveredRight?: number;
  onShowPanel?: () => void;
  emptyState?: ReactNode;
}

function Canvas({
  steps,
  agents,
  allRoles,
  issues,
  selected,
  onSelect,
  onChange,
  onAdd,
  onAddApproval,
  addMenuOpen,
  onAddMenuOpenChange,
  readOnly = false,
  status,
  panelHidden,
  coveredRight = 0,
  onShowPanel,
  emptyState,
}: WorkflowCanvasProps) {
  const flow = useReactFlow();
  // Selection lives here, fed by React Flow's own select changes, so it can't echo back and forth
  // with the page's copy. The page only hears about a single selected step.
  const [picked, setPicked] = useState<string[]>(selected ? [selected] : []);
  useEffect(() => {
    // Set from outside: a new step, a problem in the list, Escape, a click on the background.
    setPicked((p) => (selected === (p.length === 1 ? p[0] : null) ? p : selected ? [selected] : []));
  }, [selected]);
  useEffect(() => {
    setPicked((p) => (p.every((k) => steps.some((s) => s.key === k)) ? p : p.filter((k) => steps.some((s) => s.key === k))));
  }, [steps]);

  // Bring a newly selected step into view (a step just added can land off screen), clear of the
  // toolbar and the zoom controls, without changing the zoom.
  const paneWidth = useStore((s) => s.width);
  const paneHeight = useStore((s) => s.height);
  const revealed = useRef<string | null>(null);
  useEffect(() => {
    if (selected === revealed.current) return;
    revealed.current = selected;
    const step = steps.find((s) => s.key === selected);
    if (!step || !paneWidth) return;
    const { x, y, zoom } = flow.getViewport();
    const left = step.position.x * zoom + x;
    const top = step.position.y * zoom + y;
    const right = paneWidth - coveredRight;
    const inView = left >= 24 && top >= 72 && left + STEP_SIZE.width * zoom <= right - 24 && top + STEP_SIZE.height * zoom <= paneHeight - 72;
    // Centre it in the part of the canvas the panel leaves visible.
    const shift = coveredRight / 2 / zoom;
    if (!inView) void flow.setCenter(step.position.x + STEP_SIZE.width / 2 + shift, step.position.y + STEP_SIZE.height / 2, { zoom, duration: 200 });
  }, [selected, steps, flow, paneWidth, paneHeight, coveredRight]);

  const levels = useMemo(() => {
    const map = new Map<string, WorkflowIssue["level"]>();
    for (const issue of issues) for (const key of issue.steps ?? []) if (map.get(key) !== "error") map.set(key, issue.level);
    return map;
  }, [issues]);

  const built = useMemo<Node[]>(() => {
    if (!steps.length) return [];
    const xs = steps.map((s) => s.position.x);
    const center = (Math.min(...xs) + Math.max(...xs)) / 2 + STEP_SIZE.width / 2;
    const top = Math.min(...steps.map((s) => s.position.y));
    const bottom = Math.max(...steps.map((s) => s.position.y));
    return [
      { id: "@task", type: "anchor", position: { x: center - 85, y: top - 96 }, data: { kind: "task" }, style: { width: 170, height: 40 }, draggable: false, selectable: false, deletable: false, focusable: false },
      ...steps.map((s) => {
        const agent = allRoles.find((r) => r.key === s.agent && r.enabled !== false && !r.is_coordinator);
        const level = levels.get(s.key);
        const state = status?.get(s.key);
        const who = s.kind === "approval" ? "approval by you" : agent ? agent.name : "no agent";
        return {
          id: s.key,
          type: "step",
          position: s.position,
          selected: picked.includes(s.key),
          draggable: !readOnly,
          ariaLabel: `Step ${s.title || "untitled"}, ${who}${state ? `, ${state.label}` : ""}${level === "error" ? ", has a problem" : level === "warning" ? ", has a warning" : ""}`,
          data: {
            step: s,
            agent,
            issue: level,
            agents,
            solo: picked.length === 1,
            readOnly,
            status: state,
            onAddAfter: (after: string, a: Role) => onAdd(a, after),
            onAddApprovalAfter: onAddApproval && ((after: string) => onAddApproval(after)),
          } satisfies StepNodeData,
          style: { width: STEP_SIZE.width, height: STEP_SIZE.height },
        };
      }),
      { id: "@result", type: "anchor", position: { x: center - 95, y: bottom + STEP_SIZE.height + 56 }, data: { kind: "result" }, style: { width: 190, height: 40 }, draggable: false, selectable: false, deletable: false, focusable: false },
    ];
  }, [steps, allRoles, agents, levels, picked, onAdd, onAddApproval, readOnly, status]);

  // React Flow owns positions while dragging; the draft gets them when a drag ends or a step moves by arrow key.
  const [nodes, setNodes] = useState<Node[]>(built);
  useEffect(() => setNodes(built), [built]);

  const edges = useMemo<Edge[]>(() => {
    const keys = new Set(steps.map((s) => s.key));
    const awaited = new Set(steps.flatMap((s) => s.after));
    const anchor = { selectable: false, deletable: false, focusable: false, style: { strokeDasharray: "4 4" } };
    return [
      ...steps.filter((s) => !s.after.some((k) => keys.has(k))).map((s) => ({ id: `@task>${s.key}`, source: "@task", target: s.key, type: "smoothstep", ...anchor })),
      ...steps.flatMap((s) =>
        s.after
          .filter((k) => keys.has(k))
          .map((k) => ({
            id: `${k}>${s.key}`,
            source: k,
            target: s.key,
            type: "smoothstep",
            markerEnd: ARROW,
            ariaLabel: `${stepTitle(steps, s.key)} runs after ${stepTitle(steps, k)}`,
            style: { strokeWidth: 1.75 },
          })),
      ),
      ...steps.filter((s) => !awaited.has(s.key)).map((s) => ({ id: `${s.key}>@result`, source: s.key, target: "@result", type: "smoothstep", ...anchor })),
    ];
  }, [steps]);

  // Fit once the first steps have been measured; later edits keep the view where it is.
  const measured = useNodesInitialized();
  const fitted = useRef(false);
  useEffect(() => {
    if (!steps.length) fitted.current = false;
    if (fitted.current || !measured) return;
    fitted.current = true;
    void flow.fitView({ padding: 0.2, maxZoom: 1, minZoom: 0.9 });
  }, [measured, steps.length, flow]);

  const onConnect = (c: Connection) => {
    if (!c.source || !c.target || c.source.startsWith("@") || c.target.startsWith("@")) return;
    const result = connect(steps, c.source, c.target);
    if (typeof result === "string") toast.error(result);
    else onChange(() => result, "Connected steps");
  };

  return (
    <div className="relative h-full w-full">
      <div className={cn("absolute top-4 left-4 z-10 flex items-center gap-2", readOnly && "hidden")}>
        <AgentMenu
          agents={agents}
          onPick={(a) => onAdd(a, selected)}
          onPickApproval={onAddApproval && (() => onAddApproval(selected))}
          open={addMenuOpen}
          onOpenChange={onAddMenuOpenChange}
          footer={
            <>
              <DropdownSeparator />
              <p className="max-w-72 px-3 py-2 text-xs leading-5 text-text-3">
                {selected ? `The new step runs after ${stepTitle(steps, selected)}. ` : ""}Drag from a step's bottom edge to another step to connect them. Steps with nothing between them run side by side.
              </p>
            </>
          }
        >
          <Button variant="secondary" className="shadow-sm" aria-keyshortcuts="A">
            <Plus /> Add a step <ChevronDown className="text-text-3" />
          </Button>
        </AgentMenu>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={(changes: NodeChange[]) => {
          setNodes((n) => applyNodeChanges(changes, n));
          // A finished drag or an arrow-key move: keep the new positions in the draft.
          const moved = new Map<string, { x: number; y: number }>();
          for (const c of changes) {
            if (c.type !== "position" || c.dragging || !c.position || c.id.startsWith("@")) continue;
            const at = { x: Math.round(c.position.x), y: Math.round(c.position.y) };
            const step = steps.find((s) => s.key === c.id);
            if (step && (step.position.x !== at.x || step.position.y !== at.y)) moved.set(c.id, at);
          }
          if (moved.size) onChange((s) => s.map((step) => (moved.has(step.key) ? { ...step, position: moved.get(step.key)! } : step)), "Moved steps");
          const picks = changes.filter((c) => c.type === "select" && !c.id.startsWith("@"));
          if (!picks.length) return;
          const next = new Set(picked);
          for (const c of picks) if (c.type === "select") (c.selected ? next.add(c.id) : next.delete(c.id));
          const list = [...next];
          setPicked(list);
          onSelect(list.length === 1 ? list[0] : null);
        }}
        onConnect={onConnect}
        nodesConnectable={!readOnly}
        nodesDraggable={!readOnly}
        isValidConnection={(c) => !readOnly && !!c.source && !!c.target && !c.source.startsWith("@") && !c.target.startsWith("@") && c.source !== c.target}
        onDelete={({ nodes: gone, edges: cut }) => {
          const keys = gone.filter((n) => !n.id.startsWith("@")).map((n) => n.id);
          onChange((s) => {
            let next = keys.length ? removeSteps(s, keys) : s;
            for (const e of cut) if (!keys.includes(e.source) && !keys.includes(e.target)) next = disconnect(next, e.source, e.target);
            return next;
          }, keys.length ? `Deleted ${keys.map((k) => stepTitle(steps, k)).join(", ")}` : "Removed a connection");
        }}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
        onPaneClick={() => onSelect(null)}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.6}
        className="canvas-grid workflow-canvas"
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--grid-dot)" />
        {steps.length > 12 && <MiniMap pannable zoomable position="bottom-right" nodeStrokeWidth={0} maskColor="var(--xy-minimap-mask-background-color)" style={{ width: 168, height: 112 }} />}
      </ReactFlow>

      {panelHidden && onShowPanel && (
        <Tip label="Show the settings panel" side="left">
          <Button variant="secondary" size="icon" className="absolute top-4 right-4 z-10 shadow-sm" onClick={onShowPanel} aria-label="Show the settings panel">
            <PanelRight className="size-4" />
          </Button>
        </Tip>
      )}
      {steps.length > 0 && <CanvasControls onTidy={readOnly ? undefined : () => onChange(tidy, "Tidied the layout")} />}
      {!steps.length && emptyState}
    </div>
  );
}

function CanvasControls({ onTidy }: { onTidy?: () => void }) {
  const flow = useReactFlow();
  const { zoom } = useViewport();
  const btn = "flex size-9 items-center justify-center rounded-[8px] text-text-2 hover:bg-raised hover:text-text";
  return (
    <div className="absolute bottom-4 left-4 z-10 flex items-center gap-1 rounded-[12px] border border-line-soft bg-panel p-1 shadow-sm">
      <button className={btn} aria-label="Zoom out" onClick={() => void flow.zoomOut({ duration: 150 })}>
        <Minus className="size-4" />
      </button>
      <span className="w-12 text-center text-xs text-text-2 tabular-nums" aria-label="Zoom level">
        {Math.round(zoom * 100)}%
      </span>
      <button className={btn} aria-label="Zoom in" onClick={() => void flow.zoomIn({ duration: 150 })}>
        <Plus className="size-4" />
      </button>
      <span className="mx-1 h-5 w-px bg-line-soft" />
      <Tip label="Fit to screen">
        <button className={btn} aria-label="Fit to screen" onClick={() => void flow.fitView({ padding: 0.2, maxZoom: 1, minZoom: 0.9, duration: 200 })}>
          <Maximize2 className="size-4" />
        </button>
      </Tip>
      {onTidy && (
        <button
          className={cn(btn, "w-auto gap-2 px-2.5 text-sm")}
          onClick={() => {
            onTidy();
            setTimeout(() => void flow.fitView({ padding: 0.2, maxZoom: 1, minZoom: 0.9, duration: 200 }), 50);
          }}
        >
          <Wand2 className="size-4" /> Tidy up
        </button>
      )}
    </div>
  );
}

export function WorkflowCanvas(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
