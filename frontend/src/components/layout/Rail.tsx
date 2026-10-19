import {
  Gauge,
  Hand,
  History,
  Inbox,
  KanbanSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Plus,
  Settings2,
  Sun,
  Waypoints,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { useApprovals, useDashboard, useProviderStatus } from "@/api/hooks";
import { useLiveStatus } from "@/api/live";
import { ApprovalList } from "@/components/run/ApprovalCard";
import { Kbd } from "@/components/ui/display";
import { Popover, PopoverContent, PopoverTrigger, Tip } from "@/components/ui/overlays";
import { cn } from "@/lib/utils";
import { useUi } from "@/stores/ui";

function BrandMark() {
  return (
    <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
      <rect width="32" height="32" rx="7" fill="var(--raised)" />
      <circle cx="16" cy="16" r="9" fill="none" stroke="var(--line)" strokeWidth="2" />
      <circle cx="16" cy="16" r="4.5" fill="none" stroke="var(--accent)" strokeWidth="2" />
      <path d="M16 7v4M16 21v4M7 16h4M21 16h4" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="22.5" cy="9.5" r="2.2" fill="var(--st-run)" />
    </svg>
  );
}

function NavItem({ to, icon, label, badge, collapsed, end }: { to: string; icon: ReactNode; label: string; badge?: number; collapsed: boolean; end?: boolean }) {
  const link = (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "group relative flex h-8 items-center gap-2.5 rounded-control px-2 text-sm font-medium text-text-2 transition-colors hover:bg-raised hover:text-text [&_svg]:size-4 [&_svg]:shrink-0",
          isActive && "bg-raised text-text before:absolute before:top-1.5 before:bottom-1.5 before:-left-2 before:w-[2px] before:rounded-full before:bg-accent",
          collapsed && "justify-center px-0",
        )
      }
    >
      {icon}
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      {!!badge && (
        <span className={cn("rounded-full bg-sunken px-1.5 text-2xs text-text-2", collapsed && "absolute -top-0.5 -right-0.5 bg-accent text-accent-fg")}>
          {badge}
        </span>
      )}
    </NavLink>
  );
  return collapsed ? <Tip label={label} side="right">{link}</Tip> : link;
}

const LIVE_LABEL = {
  open: { text: "Live", color: "var(--st-ok)" },
  connecting: { text: "Connecting", color: "var(--st-idle)" },
  reconnecting: { text: "Reconnecting", color: "var(--st-approval)" },
  offline: { text: "Offline — retrying", color: "var(--st-fail)" },
};

export function Rail() {
  const { railCollapsed: collapsed, toggleRail, theme, setTheme, setCreate } = useUi();
  const { data: dashboard } = useDashboard();
  const { data: approvals = [] } = useApprovals();
  const { data: provider } = useProviderStatus();
  const live = useLiveStatus((s) => s.status);
  const liveMeta = LIVE_LABEL[live];

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-line-soft bg-panel transition-[width] duration-150",
        collapsed ? "w-[52px] px-1.5" : "w-[212px] px-3",
      )}
    >
      <div className={cn("flex h-12 items-center gap-2.5", collapsed && "justify-center")}>
        <BrandMark />
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <p className="text-sm font-semibold tracking-tight">Bosun</p>
            <p className="truncate text-2xs text-text-3">Agent operations</p>
          </div>
        )}
      </div>

      <button
        onClick={() => setCreate(true)}
        className={cn(
          "mt-1 mb-3 flex h-8 items-center gap-2 rounded-control border border-line bg-sunken px-2 text-sm text-text-2 transition-colors hover:border-text-3 hover:text-text",
          collapsed && "justify-center px-0",
        )}
        aria-label="New task"
      >
        <Plus className="size-4" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">New task</span>
            <Kbd>C</Kbd>
          </>
        )}
      </button>

      <nav className="flex flex-col gap-0.5" aria-label="Main">
        <NavItem to="/" end icon={<Gauge />} label="Overview" collapsed={collapsed} />
        <NavItem to="/board" icon={<KanbanSquare />} label="Board" collapsed={collapsed} />
        <NavItem to="/inbox" icon={<Inbox />} label="Inbox" badge={dashboard?.counts.untriaged} collapsed={collapsed} />
        <NavItem to="/graph" icon={<Waypoints />} label="Agent graph" badge={dashboard?.counts.active_runs} collapsed={collapsed} />
        <NavItem to="/runs" icon={<History />} label="Runs" collapsed={collapsed} />
      </nav>
      <div className="my-3 h-px bg-line-soft" />
      <nav className="flex flex-col gap-0.5" aria-label="Configuration">
        <NavItem to="/integrations" icon={<Plug />} label="Integrations" collapsed={collapsed} />
        <NavItem to="/settings" icon={<Settings2 />} label="Settings" collapsed={collapsed} />
      </nav>

      <div className="mt-auto flex flex-col gap-1 pb-3">
        <Popover>
          <PopoverTrigger asChild>
            <button
              className={cn(
                "flex h-8 items-center gap-2.5 rounded-control px-2 text-sm font-medium transition-colors hover:bg-raised",
                approvals.length ? "text-st-approval" : "text-text-3",
                collapsed && "justify-center px-0",
              )}
              aria-label={`${approvals.length} approvals waiting`}
            >
              <Hand className={cn("size-4", approvals.length && "animate-pulse-ring rounded-full")} />
              {!collapsed && <span className="flex-1 text-left">{approvals.length ? `${approvals.length} waiting for you` : "No approvals waiting"}</span>}
            </button>
          </PopoverTrigger>
          <PopoverContent className="w-[420px] p-0" align="start">
            <div className="border-b border-line-soft px-3 py-2 text-sm font-semibold">Approvals</div>
            <div className="max-h-[60vh] overflow-y-auto p-2">
              <ApprovalList approvals={approvals} empty="Agents will ask here before running commands outside their allowlist." />
            </div>
          </PopoverContent>
        </Popover>

        <div className={cn("flex items-center gap-2 px-2 py-1 text-2xs text-text-3", collapsed && "justify-center px-0")}>
          <span className="size-1.5 rounded-full" style={{ background: liveMeta.color }} />
          {!collapsed && <span>{liveMeta.text}</span>}
          {!collapsed && provider && (
            <NavLink to="/settings" className="ml-auto truncate hover:text-text-2" title={provider.summary}>
              {provider.ready ? provider.provider.replace("_", " ") : "Provider not ready"}
            </NavLink>
          )}
        </div>

        <div className={cn("flex items-center gap-1", collapsed ? "flex-col" : "px-1")}>
          <Tip label={theme === "dark" ? "Light theme" : "Dark theme"}>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="rounded-control p-1.5 text-text-3 hover:bg-raised hover:text-text"
              aria-label="Toggle theme"
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </Tip>
          <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <button onClick={toggleRail} className="rounded-control p-1.5 text-text-3 hover:bg-raised hover:text-text" aria-label="Toggle sidebar">
              {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            </button>
          </Tip>
        </div>
      </div>
    </aside>
  );
}
