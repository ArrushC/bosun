import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  type NodeChange,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { NODE_SIZE, layoutGraph } from "@/lib/graph/layout";
import type { GraphEdge, GraphNode } from "@/lib/graph/reduce";
import { STATE } from "@/lib/status";

import { nodeTypes, type NodeData } from "./nodes";

const EDGE_STYLE: Record<GraphEdge["kind"], { stroke: string; dash?: string; width?: number }> = {
  assigned: { stroke: "var(--line)" },
  delegated: { stroke: "var(--text-3)", width: 1.6 },
  planned: { stroke: "var(--line)" },
  depends: { stroke: "var(--st-dep)", dash: "4 4" },
  invoked: { stroke: "var(--line-soft)" },
  produced: { stroke: "color-mix(in srgb, var(--st-ok) 45%, transparent)" },
  message: { stroke: "var(--accent)", dash: "2 5", width: 1.1 },
  approval: { stroke: "var(--st-approval)", dash: "3 3" },
  instruction: { stroke: "var(--accent)", width: 1.6 },
  outcome: { stroke: "var(--text-3)" },
};

export interface AgentGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  direction: "LR" | "TB";
  selectedId: string | null;
  focusId: string | null;
  expanded: Set<string>;
  onSelect: (id: string | null) => void;
  onToggleExpand: (id: string) => void;
  onToggleCollapse: (id: string) => void;
  showMinimap: boolean;
  follow: boolean;
  fitSignal: number;
}

function Canvas(props: AgentGraphProps) {
  const { nodes, edges, direction, selectedId, onSelect, onToggleExpand, onToggleCollapse, expanded, showMinimap, follow, fitSignal } = props;
  const flow = useReactFlow();
  const [manual, setManual] = useState<Map<string, { x: number; y: number }>>(new Map());

  // Re-layout only when the structure changes; status updates never move nodes.
  const structureKey = useMemo(
    () => `${direction}|${nodes.map((n) => n.id).sort().join(",")}|${edges.filter((e) => e.kind !== "message").map((e) => `${e.source}>${e.target}`).sort().join(",")}`,
    [nodes, edges, direction],
  );
  const positions = useMemo(() => layoutGraph(nodes, edges, direction), [structureKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setManual(new Map()), [direction]);

  const selectedNeighbours = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set([selectedId]);
    for (const e of edges) {
      if (e.source === selectedId) set.add(e.target);
      if (e.target === selectedId) set.add(e.source);
    }
    return set;
  }, [edges, selectedId]);

  // Reuse node objects whose rendering inputs did not change, so memoised node
  // components only re-render when their own agent/tool actually changed.
  const nodeCache = useRef(new Map<string, { sig: string; node: Node }>());
  const rfNodes: Node[] = useMemo(() => {
    const next = new Map<string, { sig: string; node: Node }>();
    const out = nodes.map((n) => {
      const pos = manual.get(n.id) ?? positions.get(n.id) ?? { x: 0, y: 0 };
      const selected = n.id === selectedId;
      const dimmed = !!selectedNeighbours && !selectedNeighbours.has(n.id);
      const isExpanded = expanded.has(n.id);
      const sig = `${n.lastSeq}|${n.state}|${n.label}|${n.sublabel}|${n.data.collapsedCount ?? ""}|${pos.x},${pos.y}|${direction}|${selected}|${dimmed}|${isExpanded}`;
      const cached = nodeCache.current.get(n.id);
      if (cached && cached.sig === sig) {
        next.set(n.id, cached);
        return cached.node;
      }
      const size = NODE_SIZE[n.kind];
      const isAgent = n.kind === "agent" || n.kind === "coordinator" || n.kind === "subagent";
      const data: NodeData = {
        node: { ...n, data: { ...n.data } },
        direction,
        selected,
        dimmed,
        expanded: isExpanded,
        onToggleExpand: isAgent ? onToggleExpand : undefined,
        onToggleCollapse: isAgent || n.kind === "task" ? onToggleCollapse : undefined,
      };
      const node: Node = { id: n.id, type: n.kind, position: pos, data, width: size.width, height: size.height, selectable: true, draggable: true };
      next.set(n.id, { sig, node });
      return node;
    });
    nodeCache.current = next;
    return out;
  }, [nodes, positions, manual, direction, selectedId, selectedNeighbours, expanded, onToggleExpand, onToggleCollapse]);

  const rfEdges: Edge[] = useMemo(
    () =>
      edges.map((e) => {
        const style = EDGE_STYLE[e.kind];
        const highlighted = !!selectedNeighbours && (e.source === selectedId || e.target === selectedId);
        const dim = !!selectedNeighbours && !highlighted;
        const stroke = e.active && e.kind !== "message" ? "var(--st-run)" : style.stroke;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: e.kind === "message" || e.kind === "instruction" ? "default" : "smoothstep",
          animated: !!e.active,
          // Routed messages stay recessive until one of their agents is selected.
          label: (e.kind === "message" && highlighted) || e.kind === "instruction" ? e.label : undefined,
          labelStyle: { fill: "var(--text-2)", fontSize: 10.5, fontFamily: "var(--font-sans)" },
          labelBgStyle: { fill: "var(--bg)" },
          labelBgPadding: [4, 2] as [number, number],
          style: {
            stroke: highlighted ? "var(--accent)" : stroke,
            strokeWidth: highlighted ? 2 : (style.width ?? 1.3),
            strokeDasharray: e.active ? undefined : style.dash,
            opacity: dim ? 0.2 : e.kind === "message" && !highlighted ? 0.4 : 1,
          },
          markerEnd: e.kind === "invoked" || e.kind === "produced" ? undefined : { type: MarkerType.ArrowClosed, width: 14, height: 14, color: highlighted ? "var(--accent)" : stroke },
          zIndex: e.kind === "message" ? 1 : 0,
        } satisfies Edge;
      }),
    [edges, selectedId, selectedNeighbours],
  );

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    // Keyboard users select nodes with Enter/Space, which React Flow reports as a select change.
    const picked = changes.find((c) => c.type === "select" && c.selected);
    if (picked && picked.type === "select") onSelect(picked.id);
    const moved = changes.filter((c) => c.type === "position" && c.position);
    if (!moved.length) return;
    setManual((prev) => {
      const next = new Map(prev);
      for (const c of moved) if (c.type === "position" && c.position) next.set(c.id, c.position);
      return next;
    });
  }, [onSelect]);

  // Fit on first render, on request, and (when following live) as the graph grows.
  const lastCount = useRef(0);
  useEffect(() => {
    if (!nodes.length) return;
    const grew = nodes.length !== lastCount.current;
    lastCount.current = nodes.length;
    if (grew && follow) {
      const t = setTimeout(() => void flow.fitView({ padding: 0.18, duration: 280, maxZoom: 1.1 }), 60);
      return () => clearTimeout(t);
    }
  }, [nodes.length, follow, flow]);
  useEffect(() => {
    if (fitSignal) void flow.fitView({ padding: 0.18, duration: 280, maxZoom: 1.2 });
  }, [fitSignal, flow]);

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_, n) => onSelect(n.id)}
      onPaneClick={() => onSelect(null)}
      fitView
      fitViewOptions={{ padding: 0.18, maxZoom: 1.1 }}
      minZoom={0.15}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      nodesConnectable={false}
      elementsSelectable
      selectNodesOnDrag={false}
      onlyRenderVisibleElements={nodes.length > 150}
      className="canvas-grid"
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--grid-dot)" />
      <Controls showInteractive={false} position="bottom-left" />
      {showMinimap && (
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          nodeColor={(n) => {
            const g = (n.data as NodeData | undefined)?.node;
            if (!g) return "var(--line)";
            if (g.state === "pending") return "var(--line)";
            return g.kind === "tool" || g.kind === "toolgroup" || g.kind === "file" ? "var(--line)" : STATE[g.state].color;
          }}
          nodeStrokeWidth={0}
          maskColor="var(--xy-minimap-mask-background-color)"
          style={{ width: 168, height: 112 }}
        />
      )}
    </ReactFlow>
  );
}

export function AgentGraph(props: AgentGraphProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
