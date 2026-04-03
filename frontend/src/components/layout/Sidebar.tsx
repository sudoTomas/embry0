import { NavLink } from "react-router";
import { Activity, LayoutDashboard, Play, Workflow, Settings } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layoutStore";
import { Tooltip } from "@/components/ui/Tooltip";
import { APP_NAME } from "@/lib/branding";

const NAV_ITEMS = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/jobs", label: "Jobs", icon: Play },
  { path: "/pipelines", label: "Pipelines", icon: Workflow },
  { path: "/settings", label: "Settings", icon: Settings },
];

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
        <Activity className="h-6 w-6 shrink-0 text-primary" />
        {sidebarOpen && (
          <span className="text-lg font-bold tracking-tight">{APP_NAME}</span>
        )}
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-2">
        {NAV_ITEMS.map(({ path, label, icon: Icon }) => {
          const link = (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200",
                  "text-white/50 hover:text-white/80 hover:bg-cyan-500/[0.03] hover:translate-x-0.5",
                  isActive && "!text-orange-500 bg-orange-500/[0.08] border-l-2 border-orange-500 translate-x-0"
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {sidebarOpen && <span>{label}</span>}
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
        })}
      </nav>
    </aside>
  );
}
