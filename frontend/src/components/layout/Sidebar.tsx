import { NavLink } from "react-router";
import { LayoutDashboard, Play, Workflow, Settings, Bot, Box, CircleDot, KeyRound, Layers, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layoutStore";
import { Tooltip } from "@/components/ui/Tooltip";
import { APP_NAME } from "@/lib/branding";
import { AlchemicalSigil } from "@/components/divine/AlchemicalSigil";
import type { LucideIcon } from "lucide-react";
import type { Stage } from "@/lib/sigils";

interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  accentColor?: string;
  /**
   * Optional alchemical-stage sigil. When set AND the divine layer is
   * enabled (no body[data-divine="off"]), the sigil renders instead of
   * the lucide icon. Lucide remains the fallback for utility routes.
   */
  stage?: Stage;
}

const MONITORING_ITEMS: NavItem[] = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard, stage: "publish" },
  { path: "/issues", label: "Issues", icon: CircleDot, stage: "triage" },
  { path: "/jobs", label: "Jobs", icon: Play, stage: "develop" },
  { path: "/qa/repos", label: "QA", icon: Layers, stage: "qa" },
];

const CONFIGURATION_ITEMS: NavItem[] = [
  { path: "/agents", label: "Agents", icon: Bot, stage: "validate" },
  { path: "/sandboxes", label: "Sandboxes", icon: Box, stage: "qa" },
  { path: "/pipelines", label: "Pipelines", icon: Workflow, stage: "explore" },
  { path: "/environments", label: "Environments", icon: KeyRound },
  // Phase 5G: per-repo workspace_provider overrides admin surface.
  { path: "/qa/admin/providers", label: "Provider overrides", icon: SlidersHorizontal },
  { path: "/settings", label: "Settings", icon: Settings },
];

function NavItemLink({ item, sidebarOpen }: { item: NavItem; sidebarOpen: boolean }) {
  const { path, label, icon: Icon, accentColor, stage } = item;

  const link = (
    <NavLink
      key={path}
      to={path}
      end={path === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200",
          "text-white/50 hover:text-white/80 hover:bg-cyan-500/[0.03] hover:translate-x-0.5",
          isActive && "!text-primary bg-primary/[0.08] border-l-2 border-primary translate-x-0",
          !isActive && accentColor && "hover:translate-x-0.5"
        )
      }
    >
      {({ isActive }) => (
        <>
          {stage ? (
            <>
              {/* Sigil takes the slot when divine layer is on. */}
              <span
                className="divine-element h-4 w-4 shrink-0 flex items-center justify-center"
                style={accentColor && !isActive ? { color: accentColor } : undefined}
              >
                <AlchemicalSigil stage={stage} size={16} />
              </span>
              {/* Lucide fallback: hidden by default, shown when body[data-divine="off"]. */}
              <Icon
                className="divine-fallback h-4 w-4 shrink-0"
                style={
                  accentColor && !isActive
                    ? { color: accentColor }
                    : undefined
                }
              />
            </>
          ) : (
            <Icon
              className="h-4 w-4 shrink-0"
              style={
                accentColor && !isActive
                  ? { color: accentColor }
                  : undefined
              }
            />
          )}
          {sidebarOpen && (
            <span
              style={
                accentColor && !isActive
                  ? { color: accentColor }
                  : undefined
              }
            >
              {label}
            </span>
          )}
        </>
      )}
    </NavLink>
  );

  if (!sidebarOpen) {
    return (
      <Tooltip key={path} content={label} side="right">
        {link}
      </Tooltip>
    );
  }
  return link;
}

export function Sidebar() {
  const sidebarOpen = useLayoutStore((s) => s.sidebarOpen);

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-white/[0.06] bg-[#0c1015] transition-all duration-200",
        sidebarOpen ? "w-56" : "w-14"
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b px-3">
        <svg
          width="24"
          height="24"
          viewBox="0 0 64 64"
          aria-hidden="true"
          className="shrink-0 divine-element text-primary"
        >
          <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <circle cx="32" cy="10" r="2.4" fill="currentColor" />
          <circle cx="54" cy="32" r="2.4" fill="currentColor" />
          <circle cx="32" cy="54" r="2.4" fill="currentColor" />
          <circle cx="10" cy="32" r="2.4" fill="currentColor" />
          <line
            x1="14"
            y1="32"
            x2="50"
            y2="32"
            stroke="currentColor"
            strokeWidth="1.6"
            opacity="0.7"
          />
        </svg>
        {sidebarOpen && (
          <span className="text-lg font-bold tracking-tight">{APP_NAME}</span>
        )}
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-2">
        {sidebarOpen && (
          <div className="px-3 pt-4 pb-1">
            <span className="text-[10px] font-semibold tracking-wider text-white/20 uppercase">Monitoring</span>
          </div>
        )}
        {MONITORING_ITEMS.map((item) => (
          <NavItemLink key={item.path} item={item} sidebarOpen={sidebarOpen} />
        ))}

        <div className="mx-3 my-2 border-t border-white/[0.06]" />
        {sidebarOpen && (
          <div className="px-3 pt-2 pb-1">
            <span className="text-[10px] font-semibold tracking-wider text-white/20 uppercase">Configuration</span>
          </div>
        )}
        {CONFIGURATION_ITEMS.map((item) => (
          <NavItemLink key={item.path} item={item} sidebarOpen={sidebarOpen} />
        ))}
      </nav>
    </aside>
  );
}
