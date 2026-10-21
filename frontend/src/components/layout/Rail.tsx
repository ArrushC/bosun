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
  Workflow,
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
          "group relative flex h-10 items-center gap-3 rounded-control px-3 text-sm text-text-2 transition-colors hover:bg-raised hover:text-text [&_svg]:size-[18px] [&_svg]:shrink-0",
          isActive && "bg-raised font-medium text-text",
          collapsed && "justify-center px-0",
        )
      }
    >
      {icon}
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      {!!badge && (
        <span className={cn("rounded-full bg-sunken px-2 text-xs text-text-2", collapsed && "absolute top-0 right-0 bg-accent px-1.5 text-accent-fg")}>
          {badge}
        </span>
      )}
    </NavLink>
  );
  // The tooltip trigger would overwrite NavLink's className function, so it wraps a span.
  return collapsed ? (
    <Tip label={label} side="right">
      <span className="block">{link}</span>
    </Tip>
  ) : (
    link
  );
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
        "flex h-full shrink-0 flex-col bg-panel transition-[width] duration-150",
        collapsed ? "w-[64px] px-2" : "w-[264px] px-3",
      )}
    >
      <div className={cn("flex h-16 items-center gap-3 px-2", collapsed && "justify-center px-0")}>
        <BrandMark />
        {!collapsed && <p className="text-md font-semibold tracking-tight">Bosun</p>}
      </div>

      <button
        onClick={() => setCreate(true)}
        className={cn(
          "mt-1 mb-5 flex h-10 items-center gap-3 rounded-control bg-raised px-3 text-sm font-medium text-text transition-colors hover:bg-line-soft",
          collapsed && "justify-center px-0",
        )}
        aria-label="New task"
      >
        <Plus className="size-[18px]" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">New task</span>
            <Kbd>C</Kbd>
          </>
        )}
      </button>

      <nav className="flex flex-col gap-1" aria-label="Main">
        <NavItem to="/" end icon={<Gauge />} label="Overview" collapsed={collapsed} />
        <NavItem to="/board" icon={<KanbanSquare />} label="Board" collapsed={collapsed} />
        <NavItem to="/workflows" icon={<Workflow />} label="Workflows" collapsed={collapsed} />
        <NavItem to="/inbox" icon={<Inbox />} label="Inbox" badge={dashboard?.counts.untriaged} collapsed={collapsed} />
        <NavItem to="/graph" icon={<Waypoints />} label="Agent graph" badge={dashboard?.counts.active_runs} collapsed={collapsed} />
        <NavItem to="/runs" icon={<History />} label="Runs" collapsed={collapsed} />
      </nav>

      <div className="mt-auto flex flex-col gap-1 pb-4">
        {approvals.length > 0 && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                className={cn(
                  "mb-2 flex h-10 items-center gap-3 rounded-control bg-st-approval/10 px-3 text-sm font-medium text-st-approval transition-colors hover:bg-st-approval/15",
                  collapsed && "justify-center px-0",
                )}
                aria-label={`${approvals.length} approvals waiting`}
              >
                <Hand className="size-[18px] animate-pulse-ring rounded-full" />
                {!collapsed && <span className="flex-1 text-left">{approvals.length} waiting for you</span>}
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-[440px] p-0" align="start">
              <div className="px-4 pt-4 pb-2 text-md font-semibold">Approvals</div>
              <div className="max-h-[60vh] overflow-y-auto p-3 pt-1">
                <ApprovalList approvals={approvals} empty="Agents will ask here before running commands outside their allowlist." />
              </div>
            </PopoverContent>
          </Popover>
        )}

        <nav className="flex flex-col gap-1" aria-label="Configuration">
          <NavItem to="/integrations" icon={<Plug />} label="Integrations" collapsed={collapsed} />
          <NavItem to="/settings" icon={<Settings2 />} label="Settings" collapsed={collapsed} />
        </nav>

        {/* Connection and provider only take space when something is wrong. */}
        {(live !== "open" || (provider && !provider.ready)) && !collapsed && (
          <div className="mt-2 flex flex-col gap-1 px-3 text-xs text-text-2">
            {live !== "open" && (
              <span className="flex items-center gap-2">
                <span className="size-2 rounded-full" style={{ background: liveMeta.color }} />
                {liveMeta.text}
              </span>
            )}
            {provider && !provider.ready && (
              <NavLink to="/settings" className="text-st-fail hover:underline" title={provider.summary}>
                Provider not ready
              </NavLink>
            )}
          </div>
        )}

        <div className={cn("mt-2 flex items-center gap-1", collapsed ? "flex-col" : "px-1")}>
          <Tip label={theme === "dark" ? "Light theme" : "Dark theme"}>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="rounded-control p-2 text-text-3 hover:bg-raised hover:text-text"
              aria-label="Toggle theme"
            >
              {theme === "dark" ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
            </button>
          </Tip>
          <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <button onClick={toggleRail} className="rounded-control p-2 text-text-3 hover:bg-raised hover:text-text" aria-label="Toggle sidebar">
              {collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
            </button>
          </Tip>
        </div>
      </div>
    </aside>
  );
}
