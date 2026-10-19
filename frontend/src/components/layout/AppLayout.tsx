import { Outlet, useLocation } from "react-router";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { Breadcrumbs } from "./Breadcrumbs";
import { BriefBar } from "@/components/dashboard/BriefBar";
import { useLayoutStore } from "@/stores/layoutStore";

/**
 * The family shell: a full-width header, the sidebar under it, and the content
 * beside the sidebar. The top line of the content is the Overview's brief on
 * the root route and the breadcrumb everywhere else; both stay put while the
 * page scrolls.
 */
export function AppLayout() {
  const densityMode = useLayoutStore((s) => s.densityMode);
  const isRoot = useLocation().pathname === "/";
  return (
    <div className="flex h-screen flex-col bg-background text-foreground" data-density={densityMode}>
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {isRoot ? <BriefBar /> : <Breadcrumbs />}
          <main className="flex-1 overflow-auto p-[var(--density-padding)]">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
