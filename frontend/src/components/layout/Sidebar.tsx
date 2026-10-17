import { NavLink } from "react-router";
import {
  Bot,
  CircleDot,
  Container,
  Cpu,
  Eye,
  FolderGit2,
  Gauge,
  KeyRound,
  ListChecks,
  ScrollText,
  Settings,
  ShieldCheck,
  SquareTerminal,
  Variable,
  Workflow,
  Zap,
  UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useLayoutStore } from "@/stores/layoutStore";
import { useAuthStore } from "@/stores/authStore";
import { useAwaitingIndicator } from "@/hooks/useAwaitingIndicator";
import { useIsNarrow } from "@/hooks/useIsNarrow";
import { Tooltip } from "@/components/ui/Tooltip";
import { APP_NAME } from "@/lib/branding";
import type { LucideIcon } from "lucide-react";

// Nav is lucide-only by decision (RAV-1058, 2026-07-26): the alchemical
// sigils that used to override some entries live on in the divine layer
// elsewhere (Agents, job detail, About), not in the nav rail.
interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  accentColor?: string;
  /** RAV-1056: render only for admin-tier sessions (or API-key mode —
   *  the master key is admin-equivalent). Backend enforces regardless. */
  adminOnly?: boolean;
}

interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

// Unified IA (ticket 011, Companion surfaces dropped in RAV-1058 A2):
// Overview, Work, Pipelines & QA, Infra, Settings — embry0-backend only.
const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: "Overview",
    items: [
      { path: "/", label: "Overview", icon: Gauge },
    ],
  },
  {
    label: "Work",
    items: [
      { path: "/console", label: "Console", icon: SquareTerminal },
      { path: "/issues", label: "Issues", icon: CircleDot },
      { path: "/jobs", label: "Jobs", icon: Zap },
    ],
  },
  {
    label: "Pipelines & QA",
    items: [
      { path: "/pipelines", label: "Pipelines", icon: Workflow },
      // RAV-1301: central registry + token-discovered Add-repo flow.
      { path: "/repos", label: "Repos", icon: FolderGit2 },
      { path: "/qa/repos", label: "QA", icon: ShieldCheck },
    ],
  },
  {
    label: "Operations",
    items: [
      { path: "/watcher", label: "Watcher", icon: Eye },
      { path: "/logs", label: "Logs", icon: ScrollText },
      // RAV-1056 Phase C: global audit log. adminOnly gates NAV only —
      // operators reaching /admin/audit by URL get their own rows (backend
      // enforces row scoping).
      { path: "/admin/audit", label: "Audit", icon: ListChecks, adminOnly: true },
    ],
  },
  {
    label: "Infra",
    items: [
      { path: "/sandboxes", label: "Sandboxes", icon: Container },
      { path: "/agents", label: "Agents", icon: Bot },
      { path: "/environments", label: "Environments", icon: Variable },
      { path: "/credentials", label: "Credentials", icon: KeyRound },
    ],
  },
  {
    label: "Settings",
    items: [
      { path: "/settings", label: "Settings", icon: Settings },
      // RAV-1297: the LLM provider catalog — availability, connection
      // tests, per-model enablement, tier assignment.
      { path: "/providers", label: "LLM Providers", icon: Cpu },
      { path: "/admin/users", label: "Users", icon: UsersRound, adminOnly: true },
    ],
  },
];

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
          "relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200",
          "text-white/50 hover:text-white/80 hover:bg-cyan-500/[0.03] hover:translate-x-0.5",
          isActive && "!text-primary bg-primary/[0.08] border-l-2 border-primary translate-x-0",
          !isActive && accentColor && "hover:translate-x-0.5"
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            className="h-4 w-4 shrink-0"
            style={
              accentColor && !isActive
                ? { color: accentColor }
                : undefined
            }
          />
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
          {showBadge &&
            (sidebarOpen ? (
              <span
                data-testid="nav-awaiting-badge"
                className="ml-auto rounded-full bg-amber-400/15 px-1.5 py-0.5 text-[10px] font-mono text-amber-400"
              >
                {badge}
              </span>
            ) : (
              // Collapsed rail: the count has no room, but the amber dot
              // still says "something is blocked".
              <span
                data-testid="nav-awaiting-dot"
                className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-400"
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
        "flex flex-col border-r border-white/[0.06] bg-[#0c1015] transition-all duration-200",
        narrow ? "fixed inset-y-0 left-0 z-50 w-64 shadow-2xl" : sidebarOpen ? "w-56" : "w-14"
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
        {NAV_GROUPS.map((group, idx) => (
          <section key={group.label} aria-label={group.label}>
            {idx > 0 && <div className="mx-3 my-2 border-t border-white/[0.06]" />}
            {sidebarOpen && (
              <h3 className="px-3 pt-2 pb-1 text-[10px] font-semibold tracking-wider text-white/20 uppercase">
                {group.label}
              </h3>
            )}
            <div className="flex flex-col gap-1">
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
    </aside>
  );

  if (!narrow) return aside;
  return (
    <>
      <div
        data-testid="sidebar-backdrop"
        role="presentation"
        onClick={closeDrawer}
        className="fixed inset-0 z-40 bg-black/60"
      />
      {aside}
    </>
  );
}
