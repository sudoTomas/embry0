import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useLocation } from "react-router";
import { useLayoutStore } from "@/stores/layoutStore";
import { Button } from "@/components/ui/Button";
import { AthanorMark } from "@/components/divine/AthanorMark";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { OPERATION_FOR_ROUTE, OPERATION_NUMERAL } from "@/components/divine/operations";

const breadcrumbMap: Record<string, string> = {
  "": "Dashboard",
  jobs: "Jobs",
  issues: "Issues",
  agents: "Agents",
  sandboxes: "Sandboxes",
  pipelines: "Pipelines",
  settings: "Settings",
  templates: "Templates",
  environments: "Environments",
};

export function TopBar() {
  const { sidebarOpen, toggleSidebar } = useLayoutStore();
  const location = useLocation();
  const pathSegments = location.pathname.split("/").filter(Boolean);
  const currentPage = breadcrumbMap[pathSegments[0] ?? ""] ?? "Athanor";
  // Resolve current route to its canonical alchemical operation. Routes
  // without an entry in OPERATION_FOR_ROUTE render no glyph.
  const routeKey = pathSegments.length === 0 ? "/" : `/${pathSegments[0]}`;
  const operation = OPERATION_FOR_ROUTE[routeKey];

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
      <nav className="flex items-center gap-2" aria-label="Breadcrumb">
        <AthanorMark />
        <span className="text-white/20 mx-1">/</span>
        <span className="text-sm text-white/80 font-medium">{currentPage}</span>
        {pathSegments.length > 1 && (
          <>
            <span className="text-white/20 mx-1.5">/</span>
            <span className="text-sm text-white/50 font-mono">{pathSegments[1]}</span>
          </>
        )}
        {operation && (
          <span className="ml-3 flex items-center gap-1.5 opacity-70">
            <OperationGlyph operation={operation} size={16} titled />
            <span className="text-[10px] uppercase tracking-[0.18em] text-primary/70 font-mono">
              {OPERATION_NUMERAL[operation]} · {operation}
            </span>
          </span>
        )}
      </nav>
    </header>
  );
}
