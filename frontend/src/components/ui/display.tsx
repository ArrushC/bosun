import { Tabs as TabsPrimitive } from "radix-ui";
import { CircleDot, FileCode2, GitPullRequest, Hand, ListChecks, Loader2, PenLine, type LucideIcon } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { PRIORITY, STATE, type VisualState } from "@/lib/status";
import { cn } from "@/lib/utils";

/** Status pill: icon + label + colour; never colour alone. */
export function StatusPill({ state, label, className, compact }: { state: VisualState; label?: string; className?: string; compact?: boolean }) {
  const meta = STATE[state];
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-full border px-1.5 text-2xs font-medium whitespace-nowrap",
        meta.pattern === "dashed" && "border-dashed",
        meta.pattern === "struck" && "line-through",
        state === "waiting_approval" && "animate-pulse-ring",
        className,
      )}
      style={{ color: meta.color, borderColor: `color-mix(in srgb, ${meta.color} 45%, transparent)`, background: `color-mix(in srgb, ${meta.color} 9%, transparent)` }}
    >
      <Icon className={cn("size-3", meta.spin && "animate-spin [animation-duration:1.6s]")} />
      {!compact && (label ?? meta.label)}
    </span>
  );
}

export function StateDot({ state, className }: { state: VisualState; className?: string }) {
  const meta = STATE[state];
  return (
    <span
      aria-label={meta.label}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        meta.pattern === "dashed" && "border border-dashed bg-transparent",
        state === "running" && "shadow-[0_0_0_3px_color-mix(in_srgb,var(--st-run)_22%,transparent)]",
        state === "waiting_approval" && "animate-pulse-ring",
        className,
      )}
      style={{ background: meta.pattern === "dashed" ? "transparent" : meta.color, borderColor: meta.color }}
    />
  );
}

/** Linear-style priority bars; urgent uses a filled warning square. */
export function PriorityIcon({ priority, className }: { priority: string; className?: string }) {
  const meta = PRIORITY[priority] ?? PRIORITY.none;
  if (priority === "urgent") {
    return (
      <span title={meta.label} className={cn("inline-flex size-3.5 items-center justify-center rounded-[3px] text-[9px] font-bold text-bg", className)} style={{ background: meta.color }}>
        !
      </span>
    );
  }
  return (
    <span title={meta.label} aria-label={meta.label} className={cn("inline-flex h-3.5 items-end gap-[2px]", className)}>
      {[1, 2, 3].map((bar) => (
        <span
          key={bar}
          className="w-[3px] rounded-[1px]"
          style={{ height: `${4 + bar * 3}px`, background: bar <= meta.level - 0 && meta.level > 0 ? meta.color : "var(--line)" }}
        />
      ))}
    </span>
  );
}

export const SOURCE_ICON: Record<string, { icon: LucideIcon; label: string }> = {
  code_todo: { icon: FileCode2, label: "Code annotation" },
  markdown_task: { icon: ListChecks, label: "Markdown task" },
  todo_file: { icon: ListChecks, label: "TODO file" },
  github_issue: { icon: CircleDot, label: "GitHub issue" },
  github_pr: { icon: GitPullRequest, label: "Pull request" },
  manual: { icon: PenLine, label: "Created here" },
};

export function SourceIcon({ kind, className }: { kind: string; className?: string }) {
  const meta = SOURCE_ICON[kind] ?? SOURCE_ICON.manual;
  const Icon = meta.icon;
  return <Icon aria-label={meta.label} className={cn("size-3.5 shrink-0 text-text-2", className)} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-line bg-sunken px-1 font-mono text-[10px] text-text-2">
      {children}
    </kbd>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-text-3", className)} />;
}

export function EmptyState({ icon: Icon = Hand, title, children, action, className }: {
  icon?: ComponentType<{ className?: string; strokeWidth?: number }>;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-12 text-center", className)}>
      <Icon className="size-6 text-text-3" strokeWidth={1.5} />
      <p className="text-base font-medium text-text">{title}</p>
      {children && <div className="max-w-md text-sm text-text-2">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-[8px] border border-st-fail/40 bg-st-fail/8 px-3 py-2.5">
      <span className="mt-1 size-2 shrink-0 rounded-full bg-st-fail" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text">{title}</p>
        {children && <div className="mt-0.5 text-sm break-words text-text-2">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Notice({ tone = "info", children, className }: { tone?: "info" | "warn"; children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-[8px] border px-3 py-2 text-sm",
        tone === "warn" ? "border-st-approval/40 bg-st-approval/8 text-text" : "border-line bg-sunken text-text-2",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Md({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("prose-bosun", className)}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" /> }}
      >
        {children}
      </Markdown>
    </div>
  );
}

export function Tabs({ value, onValueChange, tabs, className, children }: {
  value: string;
  onValueChange: (v: string) => void;
  tabs: { value: string; label: ReactNode; count?: number }[];
  className?: string;
  children: ReactNode;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange} className={cn("flex min-h-0 flex-col", className)}>
      <TabsPrimitive.List className="flex shrink-0 gap-4 border-b border-line-soft px-5">
        {tabs.map((t) => (
          <TabsPrimitive.Trigger
            key={t.value}
            value={t.value}
            className="relative -mb-px flex h-9 items-center gap-1.5 border-b-2 border-transparent text-sm font-medium text-text-2 transition-colors hover:text-text data-[state=active]:border-accent data-[state=active]:text-text"
          >
            {t.label}
            {t.count !== undefined && t.count > 0 && (
              <span className="rounded-full bg-raised px-1.5 text-2xs text-text-2">{t.count}</span>
            )}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  );
}

export const TabPanel = ({ value, children, className }: { value: string; children: ReactNode; className?: string }) => (
  <TabsPrimitive.Content value={value} className={cn("min-h-0 flex-1 overflow-y-auto focus:outline-none", className)}>
    {children}
  </TabsPrimitive.Content>
);

export function KeyValue({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(96px,auto)_1fr] gap-x-4 gap-y-1.5 text-sm", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-text-3">{k}</dt>
          <dd className="min-w-0 break-words text-text">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RoleTag({ name, color, className }: { name: string; color: string; className?: string }) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1.5 rounded-[4px] bg-raised pr-1.5 pl-1 text-2xs font-medium text-text", className)}>
      <span className="h-3 w-[3px] rounded-full" style={{ background: color }} />
      {name}
    </span>
  );
}
