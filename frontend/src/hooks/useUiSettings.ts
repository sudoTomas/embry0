import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchUiSettings } from "@/api/ui";
import { SETTINGS_KEY } from "@/hooks/useSettingsDocument";
import { readStoredUiSettings, storeUiSettings, UI_DEFAULTS, type UiSettings } from "@/lib/uiSettings";

/** A change made elsewhere shows within a minute; one saved on this dashboard's Policy tab at once. */
export const UI_SETTINGS_POLL_MS = 60_000;

/** Under the settings document's key, so a save, an undo or a conflict there reads it again at once. */
export const UI_SETTINGS_KEY = [...SETTINGS_KEY, "ui"] as const;

/**
 * The read the layout makes before it draws the navigation and the page, so
 * the game never shows for a moment while it is off. `settled` once there is
 * an answer to draw with:
 *
 * - A browser that read the section before draws at once from the answer it
 *   kept (localStorage), and reads the section again behind it.
 * - A browser's first visit waits for the read, with the header alone, so a
 *   deployment that turned the game off never shows it. The read gives up
 *   after `UI_SETTINGS_TIMEOUT_MS`, is not retried and is never paused for a
 *   browser that says it is offline, so the wait is bounded.
 * - A failure (an older backend without the route, a server that does not
 *   answer in time) reads as the defaults, and a later failure keeps what the
 *   last answer said.
 */
export function useUiSettingsQuery(): { settled: boolean; settings: UiSettings } {
  const query = useQuery({
    queryKey: UI_SETTINGS_KEY,
    queryFn: async () => {
      const settings = await fetchUiSettings();
      storeUiSettings(settings);
      return settings;
    },
    initialData: readStoredUiSettings,
    // What was kept is drawn, never trusted as fresh: it is read again at once.
    initialDataUpdatedAt: 0,
    staleTime: UI_SETTINGS_POLL_MS,
    refetchInterval: UI_SETTINGS_POLL_MS,
    retry: false,
    networkMode: "always",
  });
  return { settled: !query.isPending, settings: query.data ?? UI_DEFAULTS };
}

/** What the layout read, for everything inside it; the defaults outside it (a page under test). */
export const UiSettingsContext = createContext<UiSettings>(UI_DEFAULTS);

/** The dashboard's settings in effect: whether the game shows, and the page `/` opens. */
export function useUiSettings(): UiSettings {
  return useContext(UiSettingsContext);
}
