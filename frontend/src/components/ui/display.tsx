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
        "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium whitespace-nowrap",
        meta.pattern === "dashed" && "border-dashed",
        meta.pattern === "struck" && "line-through",
        state === "waiting_approval" && "animate-pulse-ring",
        className,
      )}
      style={{ color: meta.color, borderColor: `color-mix(in srgb, ${meta.color} 45%, transparent)`, background: `color-mix(in srgb, ${meta.color} 9%, transparent)` }}
    >
      <Icon className={cn("size-3.5", meta.spin && "animate-spin [animation-duration:1.6s]")} />
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
      <span title={meta.label} className={cn("inline-flex size-4 items-center justify-center rounded-[3px] text-[10px] font-bold text-bg", className)} style={{ background: meta.color }}>
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

export function GithubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-[5px] border border-line bg-sunken px-1 font-mono text-2xs text-text-2">
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
    <div className={cn("flex flex-col items-center justify-center gap-3 px-8 py-20 text-center", className)}>
      <Icon className="size-8 text-text-3" strokeWidth={1.4} />
      <p className="text-md font-semibold text-text">{title}</p>
      {children && <div className="max-w-md text-sm leading-6 text-text-2">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNote({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-[12px] border border-st-fail/30 bg-st-fail/8 px-4 py-3.5">
      <span className="mt-2 size-2 shrink-0 rounded-full bg-st-fail" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text">{title}</p>
        {children && <div className="mt-1 text-sm break-words text-text-2">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Notice({ tone = "info", children, className }: { tone?: "info" | "warn"; children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-[12px] px-4 py-3 text-sm leading-6",
        tone === "warn" ? "border border-st-approval/30 bg-st-approval/8 text-text" : "bg-sunken text-text-2",
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
      <TabsPrimitive.List className="flex shrink-0 gap-6 border-b border-line-soft px-8">
        {tabs.map((t) => (
          <TabsPrimitive.Trigger
            key={t.value}
            value={t.value}
            className="relative -mb-px flex h-11 items-center gap-2 border-b-2 border-transparent text-sm font-medium text-text-2 transition-colors hover:text-text data-[state=active]:border-accent data-[state=active]:text-text"
          >
            {t.label}
            {t.count !== undefined && t.count > 0 && (
              <span className="rounded-full bg-raised px-2 text-xs text-text-2">{t.count}</span>
            )}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  );
}

export const TabPanel = ({ value, children, className }: { value: string; children: ReactNode; className?: string }) => (
  // Radix makes the panel a tab stop; its ring sits inside, so the panel's edges don't clip it.
  <TabsPrimitive.Content value={value} className={cn("min-h-0 flex-1 overflow-y-auto focus-visible:-outline-offset-2", className)}>
    {children}
  </TabsPrimitive.Content>
);

export function KeyValue({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(120px,auto)_1fr] gap-x-6 gap-y-3 text-sm", className)}>
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
    <span className={cn("inline-flex h-6 items-center gap-1.5 rounded-[6px] bg-raised pr-2 pl-1.5 text-xs font-medium text-text", className)}>
      <span className="size-2 rounded-full" style={{ background: color }} />
      {name}
    </span>
  );
}
