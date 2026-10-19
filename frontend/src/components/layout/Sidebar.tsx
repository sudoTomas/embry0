import { NavLink } from "react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layoutStore";
import { useAuthStore } from "@/stores/authStore";
import { useAwaitingIndicator } from "@/hooks/useAwaitingIndicator";
import { useIsNarrow } from "@/hooks/useIsNarrow";
import { Tooltip } from "@/components/ui/Tooltip";
import { OrchestratorStatus } from "./OrchestratorStatus";
import { NAV_GROUPS, type NavItem } from "./navGroups";

function NavItemLink({
  item,
  sidebarOpen,
  badge,
  onNavigate,
}: {
  item: NavItem;
  sidebarOpen: boolean;
  /** Attention count rendered as an amber pill (dot when collapsed). Hidden
   * at zero — used for the Console entry's awaiting-input count. */
  badge?: number;
  /** Phone drawer: close it once a destination is picked. */
  onNavigate?: () => void;
}) {
  const { path, label, icon: Icon, accentColor } = item;
  const showBadge = badge != null && badge > 0;

  const link = (
    <NavLink
      key={path}
      to={path}
      end={path === "/"}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "relative flex h-8 w-full items-center gap-3 text-nav font-semibold transition-colors",
          sidebarOpen ? "px-4" : "justify-center",
          "text-fg-secondary hover:bg-overlay hover:text-fg",
          isActive && "bg-overlay !text-primary",
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            className="h-[18px] w-[18px] shrink-0"
            style={
              accentColor && !isActive
                ? { color: accentColor }
                : undefined
            }
          />
          {sidebarOpen && (
            <span
              className="truncate"
              style={
                accentColor && !isActive
                  ? { color: accentColor }
                  : undefined
              }
            >
              {label}
            </span>
          )}
          {showBadge &&
            (sidebarOpen ? (
              <span
                data-testid="nav-awaiting-badge"
                className="ml-auto rounded bg-amber-dim px-1.5 py-0.5 text-2xs font-semibold tabular-nums text-warning"
              >
                {badge}
              </span>
            ) : (
              // Collapsed rail: the count has no room, but the amber dot
              // still says "something is blocked".
              <span
                data-testid="nav-awaiting-dot"
                className="absolute right-3 top-1.5 h-1.5 w-1.5 rounded-full bg-warning"
              />
            ))}
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
  const desktopOpen = useLayoutStore((s) => s.sidebarOpen);
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar);
  const drawerOpen = useLayoutStore((s) => s.drawerOpen);
  const setDrawerOpen = useLayoutStore((s) => s.setDrawerOpen);
  // Phone width: the sidebar is a drawer over the content, closed until the
  // top-bar menu button opens it, and closed again by a backdrop tap or a
  // navigation. Before this, the 224px column stayed beside a 390px screen
  // and left about 140px for the page.
  const narrow = useIsNarrow();
  const sidebarOpen = narrow ? true : desktopOpen;
  const closeDrawer = () => setDrawerOpen(false);
  // RAV-1056: admin-only entries render for admin sessions or API-key mode
  // (the master key is admin-equivalent). Backend enforces independently.
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";
  // Blocked-job (awaiting_input + paused) count: badges the Console entry
  // and mirrors into document.title ("(2⚠) …") — the sidebar is mounted on
  // every route, so the title stays honest app-wide.
  const awaitingCount = useAwaitingIndicator();

  if (narrow && !drawerOpen) return null;

  const aside = (
    <aside
      data-testid="sidebar"
      data-mode={narrow ? "drawer" : "column"}
      className={cn(
        "flex shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-200",
        // The drawer opens under the header, so the header's menu button
        // stays visible and closes it again.
        narrow ? "fixed bottom-0 left-0 top-header z-50 w-sidebar shadow-2xl" : sidebarOpen ? "w-sidebar" : "w-rail"
      )}
    >
      {!narrow && (
        <div className={cn("flex h-11 shrink-0 items-center", sidebarOpen ? "px-2.5" : "justify-center")}>
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
            className="flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-overlay hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {sidebarOpen ? <ChevronLeft className="h-[18px] w-[18px]" /> : <ChevronRight className="h-[18px] w-[18px]" />}
          </button>
        </div>
      )}

      <nav
        className={cn(
          // 32 px items and a 6 px gap leave room for the group labels, so
          // the whole nav still fits a 1440x900 screen.
          "flex flex-1 flex-col gap-1.5 pb-3",
          narrow && "pt-3",
          // The rail keeps its overflow visible so the hover labels can show.
          sidebarOpen ? "overflow-y-auto" : "overflow-visible",
        )}
      >
        {NAV_GROUPS.map((group) => (
          <section key={group.label} aria-label={group.label}>
            {/* A quiet label over each group, aligned with the item icons.
                Overview is a group of one and needs none; the collapsed rail
                has no room, so there the name stays for screen readers. */}
            <h3
              className={cn(
                sidebarOpen && group.label !== "Overview"
                  ? "truncate px-4 pb-0.5 text-2xs font-semibold uppercase tracking-wider text-fg-muted"
                  : "sr-only",
              )}
            >
              {group.label}
            </h3>
            <div className="flex flex-col">
              {group.items
                .filter((item) => !item.adminOnly || isAdmin)
                .map((item) => (
                <NavItemLink
                  key={item.path}
                  item={item}
                  sidebarOpen={sidebarOpen}
                  badge={item.path === "/console" ? awaitingCount : undefined}
                  onNavigate={narrow ? closeDrawer : undefined}
                />
              ))}
            </div>
          </section>
        ))}
      </nav>

      <div className="shrink-0 border-t border-border py-2.5">
        <OrchestratorStatus collapsed={!sidebarOpen} />
      </div>
    </aside>
  );

  if (!narrow) return aside;
  return (
    <>
      <div
        data-testid="sidebar-backdrop"
        role="presentation"
        onClick={closeDrawer}
        className="fixed inset-x-0 bottom-0 top-header z-40 bg-black/60"
      />
      {aside}
    </>
  );
}
