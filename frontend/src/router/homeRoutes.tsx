import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useUiSettings } from "@/hooks/useUiSettings";
import { homePage, opensTheGuild, pagePath, type HomePage } from "@/lib/uiSettings";

/**
 * The routes the settings document's `ui` section (#177) moves: `/` opens the
 * guild or the Overview, the Overview while the game is off; a page that can
 * be the home has one address, `/` while it is the home and its own path
 * otherwise; and the game's pages are not there while the game is off.
 */

/**
 * `/`: the page the dashboard opens on. A link that opens something in the
 * guild (`/?quest=…`, written while the guild was at `/`) is sent to the
 * guild at its own path, its query kept, while the game is on and the
 * Overview is the home.
 */
export function HomeRoute({ pages }: { pages: Record<HomePage, ReactNode> }) {
  const ui = useUiSettings();
  const { search, hash } = useLocation();
  const page = homePage(ui);
  if (ui.game && page !== "guild" && opensTheGuild(search)) {
    return <Navigate to={{ pathname: pagePath("guild", ui), search, hash }} replace />;
  }
  return <>{pages[page]}</>;
}

/** A page that can be the home, at its own path: sent to `/`, its query kept, while it is the home. */
export function OwnPathRoute({ page, children }: { page: HomePage; children: ReactNode }) {
  const ui = useUiSettings();
  const { search, hash } = useLocation();
  if (homePage(ui) === page) return <Navigate to={{ pathname: "/", search, hash }} replace />;
  return <>{children}</>;
}

/** A page of the game layer: sent to `/` while the game is off. */
export function GameRoute({ children }: { children: ReactNode }) {
  return useUiSettings().game ? <>{children}</> : <Navigate to="/" replace />;
}
