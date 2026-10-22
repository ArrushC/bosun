import { Check, FileDiff, ShieldAlert, ThumbsDown, UserCheck, X } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router";

import { useResolveApproval } from "@/api/hooks";
import type { Approval } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Md } from "@/components/ui/display";
import { Checkbox, Input, Textarea } from "@/components/ui/inputs";
import { stepStatusLabel } from "@/lib/status";
import { cn, plural, relativeTime } from "@/lib/utils";

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

interface StepContext {
  step?: { key: string; title: string };
  checks?: Array<{ key: string; title: string; kind?: string; agent?: string | null; status?: string; report?: string; session_id?: string | null }>;
  changes?: { files: number; additions: number; deletions: number; paths: string[] } | null;
  next?: Array<{ key: string; title: string }>;
  workflow?: { name: string; version: number | null } | null;
}

/** What an approval step asks a person to check: the work before it, the changes so far and what comes next. */
export function StepContextView({ approval }: { approval: Approval }) {
  const data = approval.data as StepContext;
  const checks = (data.checks ?? []).filter((c) => c.kind !== "approval" || c.report?.startsWith("Approved. Note"));
  return (
    <div className="mt-3 space-y-4 text-sm">
      {approval.description && <p className="leading-6 whitespace-pre-wrap text-text">{approval.description}</p>}
      {checks.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium tracking-wide text-text-3 uppercase">Work to check</p>
          <ul className="space-y-1.5">
            {checks.map((c) => (
              <li key={c.key}>
                <details className="group rounded-[10px] bg-sunken">
                  <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 marker:hidden">
                    <span className="min-w-0 flex-1 truncate font-medium">{c.title}</span>
                    <span className="shrink-0 text-xs text-text-3">
                      {c.kind === "approval" ? "Approver's note" : c.agent}
                      {c.status && c.kind !== "approval" ? `, ${stepStatusLabel(c.status, c.kind).toLowerCase()}` : ""}
                    </span>
                    <span className="shrink-0 text-xs text-accent-strong group-open:hidden">Show report</span>
                  </summary>
                  <div className="max-h-72 overflow-y-auto border-t border-line-soft px-3 py-2">
                    {c.report ? <Md className="text-sm">{c.report}</Md> : <p className="text-text-3">No report.</p>}
                    {c.session_id && (
                      <Link to={`/runs/${approval.run_id}?tab=transcript&session=${c.session_id}`} className="mt-2 inline-block text-xs text-accent-strong underline underline-offset-[3px]">
                        Open the transcript
                      </Link>
                    )}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.changes && (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-text-2">
          <FileDiff className="size-4 text-text-3" />
          {data.changes.files ? (
            <>
              {plural(data.changes.files, "file")} changed so far, <span className="text-st-ok">+{data.changes.additions}</span>{" "}
              <span className="text-st-fail">−{data.changes.deletions}</span>
              <Link to={`/runs/${approval.run_id}?tab=changes`} className="text-accent-strong underline underline-offset-[3px]">
                Review the changes
              </Link>
            </>
          ) : (
            "No files changed so far."
          )}
        </p>
      )}
      <p className="text-text-2">
        {data.next?.length ? (
          <>
            <span className="text-text">Approve</span> to continue with {data.next.map((n) => n.title).join(", ")}. <span className="text-text">Reject</span> to skip{" "}
            {data.next.length === 1 ? "it" : "them"} and what follows; other branches carry on.
          </>
        ) : (
          <>Nothing waits for this step: approving lets the run finish, rejecting ends it as rejected.</>
        )}
      </p>
    </div>
  );
}

export function ApprovalCard({ approval, compact }: { approval: Approval; compact?: boolean }) {
  if (approval.kind === "workflow_step") return <StepApprovalCard approval={approval} compact={compact} />;
  return <ToolApprovalCard approval={approval} compact={compact} />;
}

/** A workflow approval step: Approve or Reject, with an optional note for the steps after it. */
function StepApprovalCard({ approval, compact }: { approval: Approval; compact?: boolean }) {
  const resolve = useResolveApproval();
  const [note, setNote] = useState("");
  const pending = approval.status === "pending";
  const busy = resolve.isPending;
  const stopped = approval.run_status === "paused" || approval.run_status === "interrupted";
  // Synchronous guard: a double click must send one decision (the server would refuse the second).
  const deciding = useRef(false);
  const decide = (decision: "approve" | "deny") => {
    if (deciding.current) return;
    deciding.current = true;
    resolve.mutate({ id: approval.id, decision, message: note.trim() || undefined }, { onSettled: () => (deciding.current = false) });
  };
  const workflow = (approval.data as StepContext).workflow;
  return (
    <article className={cn("rounded-panel border bg-panel p-5", pending ? "border-st-approval/40" : "border-line-soft opacity-75")}>
      <header className="flex items-start gap-3">
        <UserCheck className="mt-0.5 size-5 shrink-0 text-st-approval" />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium break-words">{approval.title}</p>
          <p className="mt-0.5 text-xs text-text-3">
            {approval.task_key && (
              <Link to={`/runs/${approval.run_id}?tab=steps`} className="mono hover:text-text-2">
                {approval.task_key}
              </Link>
            )}{" "}
            Approval step{workflow ? ` in ${workflow.name}` : ""}, asked {relativeTime(approval.created_at)}
          </p>
        </div>
      </header>
      {!compact && <StepContextView approval={approval} />}
      {pending ? (
        <div className="mt-4 space-y-3">
          {stopped && (
            <p className="text-sm text-text-2">The run is {approval.run_status}. Your decision is saved now, and the run carries on with it when you resume.</p>
          )}
          {!compact && (
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              maxLength={4000}
              placeholder="Note (optional). If you approve, the next steps see it."
              aria-label="Note for the decision"
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => decide("approve")}>
              <Check /> Approve
            </Button>
            <Button size="sm" variant="danger" disabled={busy} onClick={() => decide("deny")}>
              <ThumbsDown /> Reject
            </Button>
            {compact && (
              <Link
                to={`/runs/${approval.run_id}?tab=steps${(approval.data as StepContext).step ? `&step=${(approval.data as StepContext).step!.key}` : ""}`}
                className="ml-auto text-sm text-accent-strong underline underline-offset-[3px]"
              >
                See what to check
              </Link>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-3 text-sm text-text-3">
          {approval.status === "approved" ? "Approved" : approval.status === "denied" ? "Rejected" : approval.status === "cancelled" ? "Cancelled: the run was cancelled before a decision" : "Expired"}
          {approval.resolved_at ? ` ${relativeTime(approval.resolved_at)}` : ""}
          {approval.resolution?.message && approval.status !== "cancelled" ? `: ${String(approval.resolution.message)}` : ""}
        </p>
      )}
    </article>
  );
}

function ToolApprovalCard({ approval, compact }: { approval: Approval; compact?: boolean }) {
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
