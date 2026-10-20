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
    <header className={cn("shrink-0 bg-bg", className)}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-8 pt-8 pb-5">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 max-w-2xl text-sm text-text-2">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  );
}
