import { Check, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { useResolveApproval } from "@/api/hooks";
import type { Approval } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Md } from "@/components/ui/display";
import { Checkbox, Input } from "@/components/ui/inputs";
import { cn, relativeTime } from "@/lib/utils";

function ToolInput({ approval }: { approval: Approval }) {
  const input = approval.tool_input ?? {};
  if (approval.tool_name === "Bash" && typeof input.command === "string") {
    return (
      <pre className="mt-3 overflow-x-auto rounded-[10px] bg-sunken px-4 py-3 font-mono text-xs leading-5 whitespace-pre-wrap text-text">
        <span className="text-text-3 select-none">$ </span>
        {input.command}
      </pre>
    );
  }
  if (Object.keys(input).length === 0) return null;
  return (
    <pre className="mt-3 max-h-40 overflow-auto rounded-[10px] bg-sunken px-4 py-3 font-mono text-xs leading-5 text-text-2">
      {JSON.stringify(input, null, 2)}
    </pre>
  );
}

function PlanPreview({ approval }: { approval: Approval }) {
  const plan = (approval.data as { plan?: { summary?: string; rationale?: string; subtasks?: Array<{ key: string; title: string; role: string; depends_on: string[]; modifies_code: boolean }> } }).plan;
  if (!plan) return null;
  return (
    <div className="mt-3 space-y-3">
      {plan.rationale && <p className="text-sm text-text-2">{plan.rationale}</p>}
      <ol className="space-y-2">
        {plan.subtasks?.map((s) => (
          <li key={s.key} className="flex items-baseline gap-3 text-sm">
            <span className="mono shrink-0 text-text-3">{s.key}</span>
            <span className="min-w-0 flex-1">
              {s.title}
              <span className="ml-2 text-xs text-text-3">
                {s.role}
                {s.depends_on.length ? `, after ${s.depends_on.join(", ")}` : ""}
                {s.modifies_code ? ", edits code" : ""}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function ApprovalCard({ approval, compact }: { approval: Approval; compact?: boolean }) {
  const resolve = useResolveApproval();
  const [remember, setRemember] = useState(false);
  const [denyOpen, setDenyOpen] = useState(false);
  const [reason, setReason] = useState("");
  const pending = approval.status === "pending";
  const isTool = approval.kind === "tool_permission";
  const busy = resolve.isPending;

  return (
    <article className={cn("rounded-panel border bg-panel p-5", pending ? "border-st-approval/40" : "border-line-soft opacity-75")}>
      <header className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 size-5 shrink-0 text-st-approval" />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium break-words">{approval.title}</p>
          <p className="mt-0.5 text-xs text-text-3">
            {approval.task_key && (
              <Link to={`/runs/${approval.run_id}`} className="mono hover:text-text-2">
                {approval.task_key}
              </Link>
            )}{" "}
            {String(approval.data?.role ?? (approval.kind === "plan" ? "coordinator" : "agent"))} asked {relativeTime(approval.created_at)}
          </p>
        </div>
      </header>
      {approval.description && !compact && (
        <div className="mt-3 text-sm text-text-2">
          {approval.kind === "plan" ? <Md>{approval.description}</Md> : approval.description}
        </div>
      )}
      {isTool && <ToolInput approval={approval} />}
      {approval.kind === "plan" && !compact && <PlanPreview approval={approval} />}

      {pending ? (
        <div className="mt-4 space-y-3">
          {denyOpen && (
            <Input
              autoFocus
              placeholder="Tell the agent why, or what to do instead (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy && !e.nativeEvent.isComposing) resolve.mutate({ id: approval.id, decision: "deny", message: reason || undefined });
              }}
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={busy}
              onClick={() => resolve.mutate({ id: approval.id, decision: "approve", remember: isTool && remember })}
            >
              <Check /> {approval.kind === "plan" ? "Approve plan" : approval.kind === "integration" ? "Re-run on latest" : "Allow"}
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              onClick={() => (denyOpen ? resolve.mutate({ id: approval.id, decision: "deny", message: reason || undefined }) : setDenyOpen(true))}
            >
              <X /> {approval.kind === "plan" ? "Reject" : approval.kind === "integration" ? "Skip subtask" : "Deny"}
            </Button>
            {isTool && (
              <label className="ml-auto flex items-center gap-2 text-sm text-text-2">
                <Checkbox checked={remember} onCheckedChange={setRemember} aria-label="Always allow this for the rest of the run" />
                Always allow in this run
              </label>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-3 text-sm text-text-3">
          {approval.status === "approved" ? "Approved" : approval.status === "denied" ? "Denied" : approval.status === "expired" ? "Expired" : "Cancelled"}
          {approval.resolution?.message ? `: ${String(approval.resolution.message)}` : ""}
        </p>
      )}
    </article>
  );
}

export function ApprovalList({ approvals, empty }: { approvals: Approval[]; empty: string }) {
  if (!approvals.length) return <p className="px-2 py-6 text-center text-sm text-text-3">{empty}</p>;
  return (
    <div className="flex flex-col gap-3">
      {approvals.map((a) => (
        <ApprovalCard key={a.id} approval={a} />
      ))}
    </div>
  );
}
