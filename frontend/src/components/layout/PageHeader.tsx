import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function PageHeader({ title, subtitle, actions, children, className }: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("shrink-0 border-b border-line-soft bg-bg", className)}>
      <div className="flex min-h-12 flex-wrap items-center gap-x-4 gap-y-2 px-5 py-2.5">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-md font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="truncate text-xs text-text-2">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  );
}
