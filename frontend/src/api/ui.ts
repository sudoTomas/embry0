import { api } from "./client";
import { normalizeUiSettings, type UiSettings } from "@/lib/uiSettings";

/**
 * How long the dashboard waits for the section before it draws with the
 * defaults: the shell waits for this read only on a browser's first visit,
 * so a server that does not answer costs at most this long, once.
 */
export const UI_SETTINGS_TIMEOUT_MS = 3_000;

/**
 * The dashboard's settings in effect, the settings document's `ui` section
 * (#177): whether the game layer shows, and the page the dashboard opens on.
 * A failure is the caller's to handle (it falls back to the defaults), so it
 * raises no toast; an older backend without the route answers 404.
 */
export async function fetchUiSettings(): Promise<UiSettings> {
  const { data } = await api.get<unknown>("/config/ui", { suppressErrorToast: true, timeout: UI_SETTINGS_TIMEOUT_MS });
  return normalizeUiSettings(data);
}
