import type { NavGroup } from "@/components/layout/navGroups";

/** A page the dashboard can open on. */
export type HomePage = "guild" | "overview";

/** The settings document's `ui` section, as `GET /config/ui` answers it (#177). */
export interface UiSettings {
  /** Whether the game layer shows: the guild, levels, outfits, achievements, the chat box. */
  game: boolean;
  /** The page `/` opens while the game is on. */
  home: HomePage;
}

/** What a deployment that never wrote the section reads: the game on, the guild as its home. */
export const UI_DEFAULTS: UiSettings = { game: true, home: "guild" };

/** An answer as the dashboard reads it: anything but `game: false` keeps the game, and only "overview" moves the home. */
export function normalizeUiSettings(raw: unknown): UiSettings {
  const answer = (raw && typeof raw === "object" ? raw : {}) as Partial<Record<keyof UiSettings, unknown>>;
  return { game: answer.game !== false, home: answer.home === "overview" ? "overview" : "guild" };
}

/** Where a browser keeps the last answer, so its next visit draws at once instead of waiting for the read. */
export const UI_SETTINGS_STORAGE_KEY = "embry0.ui-settings";

/** The last answer this browser kept; undefined when it kept none, or keeps nothing (a private window). */
export function readStoredUiSettings(): UiSettings | undefined {
  try {
    const raw = window.localStorage.getItem(UI_SETTINGS_STORAGE_KEY);
    return raw === null ? undefined : normalizeUiSettings(JSON.parse(raw));
  } catch {
    return undefined;
  }
}

/** Keep an answer for this browser's next visit. */
export function storeUiSettings(settings: UiSettings): void {
  try {
    window.localStorage.setItem(UI_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage refused: every visit waits for the read, as a first one does.
  }
}

/** What the guild keeps in its address (WorldPage): a character, a quest, a lead, a quest being written, the map. */
export const GUILD_QUERY_KEYS = ["character", "quest", "lead", "write", "view"] as const;

/** Whether a query string opens something in the guild, such as a link written while the guild was at `/`. */
export function opensTheGuild(search: string): boolean {
  const params = new URLSearchParams(search);
  return GUILD_QUERY_KEYS.some((key) => params.has(key));
}

/** The page `/` opens: the section's `home`, or the Overview while the game is off. */
export function homePage(ui: UiSettings): HomePage {
  return ui.game ? ui.home : "overview";
}

/** Where a page that can be the home lives: `/` while it is the home, else its own path. */
export function pagePath(page: HomePage, ui: UiSettings): string {
  return homePage(ui) === page ? "/" : `/${page}`;
}

/**
 * The sidebar's groups for these settings: the game's entries left out while
 * the game is off, and a page that can be the home at `/` while it is, at its
 * own path otherwise. A group left with no entry is left out.
 */
export function navGroupsFor(groups: readonly NavGroup[], ui: UiSettings): NavGroup[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items
        .filter((item) => ui.game || !item.game)
        .map((item) => (item.home ? { ...item, path: pagePath(item.home, ui) } : item)),
    }))
    .filter((group) => group.items.length > 0);
}
