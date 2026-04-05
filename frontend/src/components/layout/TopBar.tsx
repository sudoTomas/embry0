import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useLocation } from "react-router";
import { useLayoutStore } from "@/stores/layoutStore";
import { Button } from "@/components/ui/Button";

const breadcrumbMap: Record<string, string> = {
  "": "Dashboard",
  jobs: "Jobs",
  issues: "Issues",
  demo: "Demo",
  agents: "Agents",
  sandboxes: "Sandboxes",
  pipelines: "Pipelines",
  settings: "Settings",
};

export function TopBar() {
  const { sidebarOpen, toggleSidebar } = useLayoutStore();
  const location = useLocation();
  const pathSegments = location.pathname.split("/").filter(Boolean);
  const currentPage = breadcrumbMap[pathSegments[0] ?? ""] ?? "Legion";

  return (
    <header
      className="flex h-14 items-center gap-4 border-b border-white/[0.06] px-4"
      style={{ background: 'linear-gradient(180deg, rgba(12,16,21,0.95) 0%, rgba(9,9,11,0.95) 100%)' }}
    >
      <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}>
        {sidebarOpen ? (
          <PanelLeftClose className="h-4 w-4" />
        ) : (
          <PanelLeftOpen className="h-4 w-4" />
        )}
      </Button>
      <nav className="flex items-center" aria-label="Breadcrumb">
        <span className="text-sm text-white/50">Legion</span>
        <span className="text-white/20 mx-1.5">/</span>
        <span className="text-sm text-white/80 font-medium">{currentPage}</span>
        {pathSegments.length > 1 && (
          <>
            <span className="text-white/20 mx-1.5">/</span>
            <span className="text-sm text-white/50 font-mono">{pathSegments[1]}</span>
          </>
        )}
      </nav>
    </header>
  );
}
