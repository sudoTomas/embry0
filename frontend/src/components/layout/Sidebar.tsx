import { NavLink } from "react-router";
import { Activity, LayoutDashboard, Play, CircleDot, Workflow, KeyRound, Settings } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layoutStore";
import { Tooltip } from "@/components/ui/Tooltip";
import { APP_NAME } from "@/lib/branding";

const NAV_ITEMS = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/jobs", label: "Jobs", icon: Play },
  { path: "/issues", label: "Issues", icon: CircleDot },
  { path: "/pipelines", label: "Pipelines", icon: Workflow },
  { path: "/environments", label: "Environments", icon: KeyRound },
  { path: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const sidebarOpen = useLayoutStore((s) => s.sidebarOpen);

  return (
    <aside
      className={cn(
        "flex flex-col border-r bg-card transition-all duration-200",
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
                  "text-white/50 hover:text-white/80 hover:bg-white/[0.04] hover:translate-x-0.5",
                  isActive && "!text-primary bg-primary/[0.08] border-l-2 border-primary translate-x-0"
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
