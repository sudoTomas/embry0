/**
 * Runtime API-key source (RAV-1058 A1).
 *
 * The key used to be baked into the bundle at build time (`VITE_API_KEY`),
 * which shipped the secret to every browser that could load the page. The
 * source of truth is now localStorage only, entered on first visit via the
 * AuthGate prompt — the build-time bake has been removed (RAV-1058) so the
 * bundle never carries the secret. A browser with no stored key sends no
 * credentials and the AuthGate prompts for one.
 */

import { forgetStoredUiSettings } from "@/lib/uiSettingsStorage";

const STORAGE_KEY = "embry0.api-key";

// Session-scoped fallback for browsers where localStorage throws
// (private mode, storage-partitioned iframes). Lost on reload — the
// AuthGate simply re-prompts.
let memoryKey: string | null = null;

export function getStoredApiKey(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? memoryKey;
  } catch {
    return memoryKey;
  }
}

/** The key every transport (HTTP, WS subprotocol, SSE query token) must use. */
export function getApiKey(): string | null {
  return getStoredApiKey();
}

export function setStoredApiKey(key: string): void {
  memoryKey = key;
  // The dashboard's ui answer is the caller's (ADR 0029): another key may read other organizations' values.
  forgetStoredUiSettings();
  try {
    window.localStorage.setItem(STORAGE_KEY, key);
  } catch {
    // Storage disabled — memoryKey carries the session.
  }
}

export function clearStoredApiKey(): void {
  memoryKey = null;
  forgetStoredUiSettings();
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}
