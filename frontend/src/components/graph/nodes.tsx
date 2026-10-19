import { Handle, type NodeProps, Position } from "@xyflow/react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Crown,
  FileCode2,
  FilePen,
  FileText,
  Flag,
  Globe,
  Hand,
  ListChecks,
  type LucideIcon,
  Network,
  Search,
  SquareTerminal,
  User,
  Wrench,
} from "lucide-react";
import { memo } from "react";

import type { GraphNode } from "@/lib/graph/reduce";
import { STATE, type VisualState } from "@/lib/status";
import { cn, formatDuration } from "@/lib/utils";

export interface NodeData extends Record<string, unknown> {
  node: GraphNode;
  direction: "LR" | "TB";
  selected?: boolean;
  dimmed?: boolean;
  onToggleExpand?: (id: string) => void;
  onToggleCollapse?: (id: string) => void;
  expanded?: boolean;
}

export const TOOL_ICON: Record<string, LucideIcon> = {
  Bash: SquareTerminal,
  Read: FileText,
  Write: FilePen,
  Edit: FilePen,
  MultiEdit: FilePen,
  NotebookEdit: FilePen,
  Grep: Search,
  Glob: Search,
  WebFetch: Globe,
  WebSearch: Globe,
  TodoWrite: ListChecks,
};

function Handles({ direction }: { direction: "LR" | "TB" }) {
  const target = direction === "LR" ? Position.Left : Position.Top;
  const source = direction === "LR" ? Position.Right : Position.Bottom;
  return (
    <>
      <Handle type="target" position={target} className="!size-1.5 !border-0 !bg-line" isConnectable={false} />
      <Handle type="source" position={source} className="!size-1.5 !border-0 !bg-line" isConnectable={false} />
    </>
  );
}

function stateBorder(state: VisualState, selected?: boolean) {
  if (selected) return "border-accent shadow-[0_0_0_1px_var(--accent)]";
  switch (state) {
    case "running":
      return "border-st-run/70 shadow-[0_0_0_3px_color-mix(in_srgb,var(--st-run)_14%,transparent)]";
    case "waiting_approval":
      return "border-st-approval/80 animate-pulse-ring";
    case "failed":
      return "border-st-fail/70";
    case "waiting_dependency":
    case "pending":
      return "border-dashed border-line";
    case "cancelled":
    case "paused":
      return "border-line opacity-80";
    default:
      return "border-line";
  }
}

function StateIcon({ state, className }: { state: VisualState; className?: string }) {
  const meta = STATE[state];
  const Icon = meta.icon;
  return (
    <span title={meta.label} className={cn("inline-flex", className)} style={{ color: meta.color }}>
      <Icon className={cn("size-3.5", meta.spin && "animate-spin [animation-duration:1.8s]")} />
    </span>
  );
}

// -- agents ------------------------------------------------------------------------
export const AgentNode = memo(function AgentNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed, onToggleExpand, onToggleCollapse, expanded } = data as NodeData;
  const coordinator = node.kind === "coordinator";
  const sub = node.kind === "subagent";
  const Icon = coordinator ? Crown : sub ? Network : Bot;
  const tools = node.data.toolCount as number | undefined;
  return (
    <div
      className={cn(
        "relative flex h-full w-full overflow-hidden rounded-[8px] border bg-panel transition-opacity",
        stateBorder(node.state, selected),
        dimmed && "opacity-35",
        node.state === "cancelled" && "[&_.node-title]:line-through",
      )}
    >
      <Handles direction={direction} />
      <span
        className={cn("w-[3px] shrink-0", node.state === "running" && "state-edge-running")}
        style={node.state === "running" ? undefined : { background: node.color }}
      />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 px-2.5 py-1.5">
        <div className="flex items-center gap-1.5">
          <Icon className="size-3.5 shrink-0" style={{ color: node.color }} />
          <span className="node-title truncate text-[13px] font-semibold text-text">{node.label}</span>
          {sub && <span className="rounded-[3px] bg-raised px-1 text-[10px] text-text-3">subagent</span>}
          <StateIcon state={node.state} className="ml-auto" />
        </div>
        {node.sublabel && <p className="truncate text-xs text-text-2">{node.sublabel}</p>}
        <div className="flex items-center gap-2 text-[10.5px] text-text-3">
          {node.data.model && <span className="mono truncate">{String(node.data.model).replace(/^claude-/, "")}</span>}
          {node.data.attempt > 1 && <span>attempt {node.data.attempt}</span>}
          {node.data.collapsedCount ? <span>{node.data.collapsedCount} hidden</span> : null}
          <span className="ml-auto flex items-center gap-0.5">
            {onToggleExpand && tools ? (
              <button
                className="nodrag rounded px-1 hover:bg-raised hover:text-text"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleExpand(node.id);
                }}
                title={expanded ? "Group tool calls" : "Show individual tool calls"}
              >
                {expanded ? "group" : "expand"}
              </button>
            ) : null}
            {onToggleCollapse && (
              <button
                className="nodrag rounded p-0.5 hover:bg-raised hover:text-text"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleCollapse(node.id);
                }}
                aria-label={node.data.collapsedCount ? "Expand subtree" : "Collapse subtree"}
                title={node.data.collapsedCount ? "Expand subtree" : "Collapse subtree"}
              >
                {node.data.collapsedCount ? <ChevronRight className="size-3" /> : <ChevronDown className="size-3" />}
              </button>
            )}
          </span>
        </div>
      </div>
    </div>
  );
});

// -- plan --------------------------------------------------------------------------
export const SubtaskNode = memo(function SubtaskNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed } = data as NodeData;
  return (
    <div className={cn("flex h-full w-full items-center gap-2 rounded-[8px] border bg-sunken px-2.5", stateBorder(node.state, selected), dimmed && "opacity-35")}>
      <Handles direction={direction} />
      <span className="mono flex h-5 shrink-0 items-center rounded-[4px] border border-line px-1 text-[10.5px] text-text-2">{node.sublabel}</span>
      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-xs font-medium text-text", node.state === "cancelled" && "line-through")}>{node.label}</p>
        <p className="flex items-center gap-1 text-[10.5px] text-text-3">
          <span className="size-1.5 rounded-full" style={{ background: node.color }} />
          {node.data.role}
          {node.data.modifiesCode ? ", edits code" : ""}
        </p>
      </div>
      <StateIcon state={node.state} />
    </div>
  );
});

// -- task / outcome / operator ------------------------------------------------------
export const TaskNode = memo(function TaskNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed } = data as NodeData;
  return (
    <div className={cn("flex h-full w-full flex-col justify-center rounded-[10px] border bg-raised px-3", stateBorder(node.state, selected), dimmed && "opacity-35")}>
      <Handles direction={direction} />
      <div className="flex items-center gap-2 text-[11px] text-text-3">
        <span className="mono shrink-0">{node.sublabel}</span>
        {node.data.branch && <span className="mono truncate">{String(node.data.branch)}</span>}
        <StateIcon state={node.state} className="ml-auto" />
      </div>
      <p className="mt-0.5 line-clamp-2 text-[13px] leading-[17px] font-semibold text-text">{node.label}</p>
    </div>
  );
});

export const OutcomeNode = memo(function OutcomeNode({ data }: NodeProps) {
  const { node, direction, selected } = data as NodeData;
  const color = STATE[node.state].color;
  return (
    <div className={cn("flex h-full w-full items-center gap-2.5 rounded-[10px] border bg-panel px-3", selected ? "border-accent" : "")} style={{ borderColor: selected ? undefined : `color-mix(in srgb, ${color} 60%, transparent)` }}>
      <Handles direction={direction} />
      <Flag className="size-4 shrink-0" style={{ color }} />
      <div className="min-w-0">
        <p className="truncate text-[13px] font-semibold text-text">{node.label}</p>
        <p className="truncate text-[11px] text-text-3">{node.sublabel ?? (node.data.diff ? `${node.data.diff.files} files changed` : "Run finished")}</p>
      </div>
    </div>
  );
});

export const OperatorNode = memo(function OperatorNode({ data }: NodeProps) {
  const { direction, selected } = data as NodeData;
  return (
    <div className={cn("flex h-full w-full items-center justify-center gap-2 rounded-full border bg-panel text-[12px] font-semibold", selected ? "border-accent" : "border-accent/50 text-accent-strong")}>
      <Handles direction={direction} />
      <User className="size-3.5" /> You
    </div>
  );
});

// -- tools, files, approvals ------------------------------------------------------------
export const ToolNode = memo(function ToolNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed } = data as NodeData;
  const Icon = TOOL_ICON[node.label] ?? Wrench;
  const call = node.data.call as { durationMs?: number | null; denied?: string } | undefined;
  const count = node.data.count as number | undefined;
  return (
    <div
      className={cn(
        "flex h-full w-full items-center gap-2 rounded-full border bg-panel pr-3 pl-2.5",
        stateBorder(node.state, selected),
        node.state === "pending" || node.state === "waiting_dependency" ? "border-solid border-line-soft" : "",
        dimmed && "opacity-35",
      )}
    >
      <Handles direction={direction} />
      <Icon className="size-3.5 shrink-0 text-text-2" />
      <div className="min-w-0 flex-1 leading-tight">
        <p className="flex items-center gap-1 text-[11.5px] font-semibold text-text">
          {node.label}
          {count && count > 1 ? <span className="rounded-full bg-raised px-1 text-[10px] font-medium text-text-2">{count}</span> : null}
        </p>
        <p className="mono truncate text-[10.5px] text-text-3">{call?.denied ? "blocked by boundary" : node.sublabel}</p>
      </div>
      {call?.durationMs ? <span className="text-[10px] text-text-3">{formatDuration(call.durationMs)}</span> : null}
      <StateIcon state={node.state} />
    </div>
  );
});

export const FileNode = memo(function FileNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed } = data as NodeData;
  return (
    <div className={cn("flex h-full w-full items-center gap-2 rounded-[6px] border border-line-soft bg-sunken px-2.5", selected && "border-accent", dimmed && "opacity-35")}>
      <Handles direction={direction} />
      <FileCode2 className="size-3.5 shrink-0 text-st-ok" />
      <div className="min-w-0 leading-tight">
        <p className="mono truncate text-[11.5px] text-text">{node.label}</p>
        <p className="mono truncate text-[10px] text-text-3">{node.sublabel}</p>
      </div>
    </div>
  );
});

export const ApprovalNode = memo(function ApprovalNode({ data }: NodeProps) {
  const { node, direction, selected, dimmed } = data as NodeData;
  return (
    <div className={cn("flex h-full w-full items-center gap-2 rounded-[8px] border bg-panel px-2.5", stateBorder(node.state, selected), dimmed && "opacity-35")}>
      <Handles direction={direction} />
      <Hand className="size-4 shrink-0 text-st-approval" />
      <div className="min-w-0 flex-1 leading-tight">
        <p className="truncate text-[12px] font-semibold text-text">{node.label}</p>
        <p className="truncate text-[10.5px] text-text-3">
          {node.state === "waiting_approval" ? "Waiting for you" : STATE[node.state].label}
          {node.sublabel ? `, ${node.sublabel}` : ""}
        </p>
      </div>
    </div>
  );
});

export const nodeTypes = {
  task: TaskNode,
  coordinator: AgentNode,
  agent: AgentNode,
  subagent: AgentNode,
  subtask: SubtaskNode,
  tool: ToolNode,
  toolgroup: ToolNode,
  file: FileNode,
  approval: ApprovalNode,
  outcome: OutcomeNode,
  operator: OperatorNode,
};
