/**
 * Where a browser keeps the last `GET /config/ui` answer, so its next visit
 * draws at once instead of waiting for the read (`@/lib/uiSettings`).
 *
 * The answer is the caller's: a key whose repository allowlist covers some
 * organizations reads theirs (ADR 0029). So the API-key store forgets it
 * when this browser signs in with another key or signs out. This module
 * imports nothing, so the sign-in screen, which imports the key store, does
 * not pull in the dashboard's navigation.
 */

export const UI_SETTINGS_STORAGE_KEY = "embry0.ui-settings";

/** Forget the kept answer: the next view waits for its own caller's. */
export function forgetStoredUiSettings(): void {
  try {
    window.localStorage.removeItem(UI_SETTINGS_STORAGE_KEY);
  } catch {
    // Storage refused: nothing was kept.
  }
}
