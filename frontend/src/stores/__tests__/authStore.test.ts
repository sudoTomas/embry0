import { describe, it, expect, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useAuthStore, useIsAdmin } from "../authStore";

/**
 * useIsAdmin decides whether the admin-only controls are offered. A key
 * session is admin only when the backend reported the key's tier as admin
 * (#101); a backend that does not report it (keyTier undefined) keeps the
 * pre-#101 behaviour so a new dashboard on an old API never hides controls.
 */
beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    sessionExpired: false,
    authMode: null,
    user: null,
    keyTier: undefined,
    ssoEnabled: false,
    apiKeyLogin: true,
  });
});

describe("useIsAdmin", () => {
  it("is true for a key session whose tier is admin (the master key)", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "admin" });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(true);
  });

  it("is false for a key session whose tier is operator", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "operator" });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current, "an operate-scope key must not be offered admin clicks").toBe(false);
  });

  it("is true for a key session on a backend that does not report the tier", () => {
    useAuthStore.setState({ authMode: "key", keyTier: undefined });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current, "legacy backend: keep the pre-#101 behaviour").toBe(true);
  });

  it("is true for a key session the server let in without a key (auth off)", () => {
    // AUTH_DEV_MODE: /auth/check reports key_tier null and /queue still
    // answers 200. The server skips require_admin too, so hiding the
    // controls would refuse clicks the server accepts.
    useAuthStore.setState({ authMode: "key", keyTier: null });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current, "a server with auth off grants admin").toBe(true);
  });

  it("is false for a key session whose tier is read", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "read" });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);
  });

  it("is true for an admin SSO session", () => {
    useAuthStore.setState({
      authMode: "session",
      user: { email: "a@example.com", name: "A", tier: "admin" },
    });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(true);
  });

  it("is false for an operator SSO session", () => {
    useAuthStore.setState({
      authMode: "session",
      user: { email: "op@example.com", name: "Op", tier: "operator" },
    });
    const { result } = renderHook(() => useIsAdmin());
    expect(result.current).toBe(false);
  });

  it("markAuthenticated stores the key tier for a key session", () => {
    useAuthStore.getState().markAuthenticated({ mode: "key", keyTier: "read" });
    expect(useAuthStore.getState().keyTier).toBe("read");
  });

  it("requirePrompt clears the key tier", () => {
    useAuthStore.setState({ authMode: "key", keyTier: "operator" });
    useAuthStore.getState().requirePrompt();
    expect(useAuthStore.getState().keyTier).toBeUndefined();
  });
});
