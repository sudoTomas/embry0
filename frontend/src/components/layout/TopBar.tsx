import { KeyRound, LogOut, Menu, X } from "lucide-react";
import { useLayoutStore } from "@/stores/layoutStore";
import { useAuthStore } from "@/stores/authStore";
import { ssoLogout } from "@/components/auth/AuthGate";
import { Button } from "@/components/ui/Button";
import { Embry0Mark } from "@/components/divine/Embry0Mark";
import { useIsNarrow } from "@/hooks/useIsNarrow";

/**
 * The family header: a full-width 65px bar on the surface. The lockup and the
 * tagline on the left, the signed-in identity and sign-out on the right. At
 * phone width a menu button opens the sidebar drawer; at desktop width the
 * sidebar's own chevron collapses it. The lockup's mark draws itself once as
 * the dashboard opens; it is the only mark that does.
 */
export function TopBar() {
  const drawerOpen = useLayoutStore((s) => s.drawerOpen);
  const toggleDrawer = useLayoutStore((s) => s.toggleDrawer);
  const narrow = useIsNarrow();
  const authMode = useAuthStore((s) => s.authMode);
  const user = useAuthStore((s) => s.user);

  return (
    <header className="flex h-header shrink-0 items-center gap-3 border-b border-border bg-surface px-3 sm:px-5">
      {narrow && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleDrawer}
          aria-label={drawerOpen ? "Close menu" : "Open menu"}
        >
          {drawerOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </Button>
      )}
      <Embry0Mark draw />
      <span className="ml-3 hidden truncate text-sm text-fg-muted md:inline">
        Software that is alive
      </span>
      {authMode === "session" && user && (
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <span
            className="max-w-[40vw] truncate text-sm text-fg-secondary sm:max-w-none"
            title={`${user.email} (tier: ${user.tier})`}
          >
            {user.email}
          </span>
          <Button variant="ghost" size="icon" onClick={() => void ssoLogout()} aria-label="Sign out">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      )}
      {authMode === "key" && (
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-fg-muted">
          <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
          API key
        </span>
      )}
    </header>
  );
}
