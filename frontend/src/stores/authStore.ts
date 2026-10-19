import { create } from "zustand";

/**
 * Auth gate state (RAV-1058 A1; SSO added in RAV-1069).
 *
 * - `checking`: initial probe in flight — render nothing yet.
 * - `authenticated`: probe succeeded (valid key, an SSO session with an
 *   embry0 role, or the backend runs with auth disabled) — render the app.
 * - `prompt`: the backend rejected our credentials (or we have none) —
 *   redirect to the IdP login (SSO deployments) or render the neutral
 *   API-key form (SSO-less deployments).
 * - `denied`: a VALID Auth0 session whose account carries no embry0 role
 *   (default-deny gate) — render access-denied, not the login prompt;
 *   retrying login can't fix a missing role.
 *
 * The axios 401 interceptor flips any live session back to `prompt`, so a
 * key revoked mid-session re-prompts instead of degrading every tile to
 * "Unavailable".
 */
export type AuthStatus = "checking" | "authenticated" | "prompt" | "denied";

export type AuthMode = "key" | "session";

export interface SsoUser {
  email: string;
  name: string;
  tier: string;
}

interface AuthState {
  status: AuthStatus;
  /** Whether the prompt is showing because a previously-working key got rejected. */
  sessionExpired: boolean;
  /** How the current session authenticated (null until authenticated). */
  authMode: AuthMode | null;
  /** SSO identity when authMode === "session". */
  user: SsoUser | null;
  /** Whether the backend reports SSO configured (drives the SSO button). */
  ssoEnabled: boolean;
  /** Whether the API-key entry form may be shown (false on the public
   *  SSO-only origin; bearer-header API auth is unaffected). */
  apiKeyLogin: boolean;
  markAuthenticated: (opts?: { mode?: AuthMode; user?: SsoUser | null }) => void;
  requirePrompt: (opts?: { sessionExpired?: boolean }) => void;
  markDenied: (user: SsoUser | null) => void;
  setSsoEnabled: (enabled: boolean) => void;
  setApiKeyLogin: (enabled: boolean) => void;
}

export const useAuthStore = create<AuthState>()((set) => ({
  status: "checking",
  sessionExpired: false,
  authMode: null,
  user: null,
  ssoEnabled: false,
  apiKeyLogin: true,
  markAuthenticated: (opts) =>
    set({
      status: "authenticated",
      sessionExpired: false,
      authMode: opts?.mode ?? "key",
      user: opts?.user ?? null,
    }),
  requirePrompt: (opts) =>
    set((s) => ({
      status: "prompt",
      authMode: null,
      user: null,
      // Only an authenticated session "expires"; the initial probe failing
      // is just a first visit. A failed retry while already prompting keeps
      // the existing flag so the "session expired" note doesn't vanish.
      sessionExpired:
        opts?.sessionExpired ?? (s.status === "authenticated" ? true : s.sessionExpired),
    })),
  markDenied: (user) => set({ status: "denied", authMode: "session", user }),
  setSsoEnabled: (enabled) => set({ ssoEnabled: enabled }),
  setApiKeyLogin: (enabled) => set({ apiKeyLogin: enabled }),
}));

/**
 * Whether this session may use the API's admin-only routes (require_admin):
 * the API key does, and an SSO user does when their tier is admin. Controls
 * for those routes read this, so an operator is never offered a click that
 * can only fail with 403.
 */
export function useIsAdmin(): boolean {
  return useAuthStore((s) => s.authMode === "key" || s.user?.tier === "admin");
}
