import { useState } from "react";
import { Outlet, useLocation } from "react-router";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { Breadcrumbs } from "./Breadcrumbs";
import { BriefBar } from "@/components/dashboard/BriefBar";
import { Chatbox } from "@/components/progression/Chatbox";
import { useLayoutStore } from "@/stores/layoutStore";
import { cn } from "@/lib/utils";

/**
 * The family shell: a full-width header, the sidebar under it, and the content
 * beside the sidebar. The top line of the content is the Overview's brief on
 * the root route and the breadcrumb everywhere else; both stay put while the
 * page scrolls. Each page arrives as it opens; the shell around it stays put.
 */
export function AppLayout() {
  const densityMode = useLayoutStore((s) => s.densityMode);
  const { pathname } = useLocation();
  const isRoot = pathname === "/";
  // Below sm the brief wraps, so its height waits on its answers; growing to
  // it under a page already in view pushed that page down. Until the answers
  // are in, main is laid out but not drawn there, and both arrive together.
  const [briefReady, setBriefReady] = useState(false);
  return (
    <div className="flex h-dvh flex-col bg-background text-foreground" data-density={densityMode}>
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {isRoot ? <BriefBar onReady={setBriefReady} /> : <Breadcrumbs />}
          <main
            className={cn(
              "flex-1 overflow-auto overscroll-contain [scrollbar-gutter:stable] p-[var(--density-padding)]",
              isRoot && !briefReady && "max-sm:invisible",
            )}
          >
            {/* Keyed by the path, so a page that opens arrives (.motion-enter)
                instead of swapping in a frame, while a search change (a tab, a
                filter) keeps the page and its state. One grid cell at least as
                tall as the content area: a page that fills that height (h-full)
                still can, and a long page still ends on main's bottom padding. */}
            <div
              key={pathname}
              className="motion-enter grid min-h-full grid-cols-[minmax(0,1fr)] grid-rows-[1fr] items-start"
            >
              <Outlet />
            </div>
          </main>
        </div>
      </div>
      {/* Levels reached and achievements unlocked, one message at a time (spec 2026-10-24). */}
      <Chatbox />
    </div>
  );
}
