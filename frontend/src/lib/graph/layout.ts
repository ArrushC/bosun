import dagre from "@dagrejs/dagre";

import type { GraphEdge, GraphNode, NodeKind } from "./reduce";

export const NODE_SIZE: Record<NodeKind, { width: number; height: number }> = {
  task: { width: 260, height: 74 },
  coordinator: { width: 236, height: 78 },
  agent: { width: 236, height: 78 },
  subagent: { width: 214, height: 66 },
  subtask: { width: 220, height: 58 },
  tool: { width: 196, height: 44 },
  toolgroup: { width: 196, height: 48 },
  file: { width: 190, height: 44 },
  approval: { width: 220, height: 56 },
  outcome: { width: 220, height: 60 },
  operator: { width: 120, height: 44 },
};

export type Direction = "LR" | "TB";

/**
 * Hierarchical layout (dagre). Only the structure determines positions, so
 * status updates never move nodes; positions change only when nodes are added.
 */
export function layoutGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  direction: Direction,
): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({
    rankdir: direction,
    nodesep: direction === "LR" ? 14 : 22,
    ranksep: direction === "LR" ? 54 : 52,
    edgesep: 10,
    marginx: 24,
    marginy: 24,
  });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { ...NODE_SIZE[n.kind] });
  for (const e of edges) {
    // Messages and dependency hints should not distort the hierarchy.
    if (e.kind === "message" || e.kind === "instruction") continue;
    g.setEdge(e.source, e.target, { weight: e.kind === "depends" ? 0.5 : 1, minlen: 1 }, e.id);
  }
  dagre.layout(g);
  const positions = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const p = g.node(n.id);
    if (!p) continue;
    const size = NODE_SIZE[n.kind];
    positions.set(n.id, { x: p.x - size.width / 2, y: p.y - size.height / 2 });
  }
  return positions;
}
