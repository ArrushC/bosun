import { Check, OctagonAlert, TriangleAlert } from "lucide-react";
import { type ReactNode, useRef } from "react";

import type { Role, WorkflowIssue } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/display";
import { DropdownContent, DropdownItem, DropdownLabel, DropdownMenu, DropdownTrigger } from "@/components/ui/overlays";
import { PROFILE_LABEL, roleColor } from "@/lib/status";
import { cn } from "@/lib/utils";

import type { SaveState } from "./useWorkflowDraft";

/** Agents a step can use: enabled, and not the team coordinator. */
export function stepAgents(roles: Role[]): Role[] {
  return roles.filter((r) => r.enabled && !r.is_coordinator);
}

export function agentColor(role: Pick<Role, "key" | "color"> | undefined): string {
  return role ? roleColor(role.key, role.color) : "var(--line)";
}

/**
 * An "add a step" menu: pick an agent and a step for it is added. Focus then goes to the new
 * step's settings (the page puts it there) rather than back to the button, which may be gone.
 */
export function AgentMenu({
  children,
  agents,
  onPick,
  align = "start",
  open,
  onOpenChange,
  footer,
}: {
  children: ReactNode;
  agents: Role[];
  onPick: (agent: Role) => void;
  align?: "start" | "end" | "center";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  footer?: ReactNode;
}) {
  const picked = useRef(false);
  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownTrigger asChild>{children}</DropdownTrigger>
      <DropdownContent
        align={align}
        onCloseAutoFocus={(e) => {
          if (picked.current) e.preventDefault();
          picked.current = false;
        }}
      >
        <AgentMenuItems
          agents={agents}
          onPick={(a) => {
            picked.current = true;
            onPick(a);
          }}
        />
        {footer}
      </DropdownContent>
    </DropdownMenu>
  );
}

function AgentMenuItems({ agents, onPick }: { agents: Role[]; onPick: (agent: Role) => void }) {
  return (
    <>
      <DropdownLabel>Agent step</DropdownLabel>
      {agents.map((a) => (
        <DropdownItem key={a.key} onSelect={() => onPick(a)}>
          <span className="size-2.5 shrink-0 rounded-full" style={{ background: agentColor(a) }} />
          <span className="flex-1">{a.name}</span>
          <span className="pl-4 text-xs text-text-3">{PROFILE_LABEL[a.permission_profile] ?? a.permission_profile}</span>
        </DropdownItem>
      ))}
      {agents.length === 0 && <p className="px-3 py-2 text-sm text-text-3">No agents are turned on. Turn one on in Settings.</p>}
    </>
  );
}

export function SaveStatus({ state, onRetry }: { state: SaveState; onRetry: () => void }) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center gap-1.5 text-sm">
      {state === "saved" && (
        <span className="inline-flex items-center gap-1.5 text-text-3">
          <Check className="size-4" /> Saved
        </span>
      )}
      {state === "saving" && (
        <span className="inline-flex items-center gap-1.5 text-text-3">
          <Spinner className="size-3.5" /> Saving…
        </span>
      )}
      {state === "retrying" && (
        <>
          <span className="inline-flex items-center gap-1.5 text-st-fail">
            <OctagonAlert className="size-4" /> Not saved, retrying
          </span>
          <Button size="xs" variant="ghost" onClick={onRetry}>
            Retry now
          </Button>
        </>
      )}
      {state === "conflict" && (
        <span className="inline-flex items-center gap-1.5 text-st-fail">
          <OctagonAlert className="size-4" /> Not saved
        </span>
      )}
    </span>
  );
}

export function IssueIcon({ level, className }: { level: WorkflowIssue["level"]; className?: string }) {
  return level === "error" ? (
    <OctagonAlert aria-label="Problem" className={cn("size-4 shrink-0 text-st-fail", className)} />
  ) : (
    <TriangleAlert aria-label="Warning" className={cn("size-4 shrink-0 text-st-approval", className)} />
  );
}

export function countIssues(issues: WorkflowIssue[]): string {
  const errors = issues.filter((i) => i.level === "error").length;
  const warnings = issues.length - errors;
  const parts = [
    errors ? `${errors} problem${errors === 1 ? "" : "s"}` : null,
    warnings ? `${warnings} warning${warnings === 1 ? "" : "s"}` : null,
  ];
  return parts.filter(Boolean).join(", ");
}
