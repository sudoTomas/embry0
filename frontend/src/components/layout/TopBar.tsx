import { LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useLocation } from "react-router";
import { useLayoutStore } from "@/stores/layoutStore";
import { useAuthStore } from "@/stores/authStore";
import { ssoLogout } from "@/components/auth/AuthGate";
import { Button } from "@/components/ui/Button";
import { Embry0Mark } from "@/components/divine/Embry0Mark";
import { useIsNarrow } from "@/hooks/useIsNarrow";

const breadcrumbMap: Record<string, string> = {
  // "Overview" matches the sidebar label — the RAV-1058 IA sweep retired the
  // competing "Dashboard" name.
  "": "Overview",
  console: "Console",
  jobs: "Jobs",
  issues: "Issues",
  agents: "Agents",
  sandboxes: "Sandboxes",
  pipelines: "Pipelines",
  qa: "QA",
  watcher: "Watcher",
  logs: "Logs",
  settings: "Settings",
  environments: "Environments",
  about: "About",
  admin: "Admin",
  repos: "Repos",
};

/** Known static second segments get a proper label; dynamic segments (job
 * ids, repo slugs) keep the raw mono rendering. */
const secondSegmentMap: Record<string, string> = {
  audit: "Audit",
  users: "Users",
  onboard: "Onboard",
  operations: "Operations",
  repos: "Repos",
  runs: "Runs",
  admin: "Admin",
};

export function TopBar() {
  const { sidebarOpen, drawerOpen, toggleSidebar, toggleDrawer } = useLayoutStore();
  // Phone width: the button opens and closes the drawer; desktop: it widens
  // and narrows the column.
  const narrow = useIsNarrow();
  const panelOpen = narrow ? drawerOpen : sidebarOpen;
  const onToggle = narrow ? toggleDrawer : toggleSidebar;
  const toggleLabel = narrow
    ? panelOpen ? "Close menu" : "Open menu"
    : panelOpen ? "Close sidebar" : "Open sidebar";
  const location = useLocation();
  const authMode = useAuthStore((s) => s.authMode);
  const user = useAuthStore((s) => s.user);
  const pathSegments = location.pathname.split("/").filter(Boolean);
  const currentPage = breadcrumbMap[pathSegments[0] ?? ""] ?? "embry0";

  return (
    <header
      className="flex h-14 items-center gap-4 border-b border-white/[0.06] px-4"
      style={{ background: 'linear-gradient(180deg, rgba(12,16,21,0.95) 0%, rgba(9,9,11,0.95) 100%)' }}
    >
      <Button variant="ghost" size="icon" onClick={onToggle} aria-label={toggleLabel}>
        {panelOpen ? (
          <PanelLeftClose className="h-4 w-4" />
        ) : (
          <PanelLeftOpen className="h-4 w-4" />
        )}
      </Button>
      <nav className="flex items-center gap-2" aria-label="Breadcrumb">
        <Embry0Mark />
        <span className="text-white/20 mx-1">/</span>
        <span className="text-sm text-white/80 font-medium">{currentPage}</span>
        {pathSegments.length > 1 && (
          <>
            <span className="text-white/20 mx-1.5">/</span>
            {secondSegmentMap[pathSegments[1]] ? (
              <span className="text-sm text-white/50 font-medium">{secondSegmentMap[pathSegments[1]]}</span>
            ) : (
              <span className="text-sm text-white/50 font-mono">{pathSegments[1]}</span>
            )}
          </>
        )}
      </nav>
      {authMode === "session" && user && (
        <div className="ml-auto flex items-center gap-2">
          <span
            className="max-w-[40vw] truncate text-xs text-white/50 sm:max-w-none"
            title={`${user.email} (tier: ${user.tier})`}
          >
            {user.email}
          </span>
          <Button variant="ghost" size="icon" onClick={() => void ssoLogout()} aria-label="Sign out">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      )}
    </header>
  );
}
