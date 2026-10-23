import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldX } from "lucide-react";
import { isAxiosError } from "axios";
import { api } from "@/api/client";
import { setStoredApiKey } from "@/lib/apiKey";
import { useAuthStore, type SsoUser } from "@/stores/authStore";
import { APP_NAME } from "@/lib/branding";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

/**
 * Runtime auth gate (RAV-1058 A1; SSO added in RAV-1069).
 *
 * Boot order: probe `/auth/check` first (cheap, unauthenticated). A live SSO
 * session with an embry0 role renders the app; a session WITHOUT a role
 * renders access-denied (default-deny — retrying login can't fix a missing
 * role). Otherwise fall back to the API-key probe exactly as before. The
 * axios interceptor routes any later 401 back here.
 *
 * There is deliberately NO in-repo login screen when SSO is configured: an
 * unauthenticated visitor is redirected straight to `/api/v1/auth/login`,
 * so the identity provider's own (per-tenant brandable) login page is the
 * only login surface. Customer-specific copy belongs in the IdP's
 * customization, never in this repository. The neutral API-key form exists
 * solely for deployments with SSO unset.
 */

// /queue is the cheapest always-on authed endpoint — the key probe of choice.
const PROBE_PATH = "/queue";

interface AuthCheck {
  sso_enabled: boolean;
  authenticated: boolean;
  tier: string | null;
  api_key_login?: boolean;
  /** The Bearer key's tier (#101); absent on an older backend. */
  key_tier?: string | null;
}

/** The tier of `candidate` as the sign-in check reports it; undefined when
 *  the check fails or does not report it (the store then keeps the old
 *  every-key-is-admin behaviour). */
async function fetchKeyTier(candidate: string): Promise<string | null | undefined> {
  try {
    const check = await api.get<AuthCheck>("/auth/check", {
      headers: { Authorization: `Bearer ${candidate}` },
      suppressErrorToast: true,
    });
    return check.data.key_tier;
  } catch {
    return undefined;
  }
}

async function fetchSsoUser(): Promise<SsoUser | null> {
  try {
    const me = await api.get<{ email: string; name: string; tier: string }>("/auth/me");
    return { email: me.data.email, name: me.data.name, tier: me.data.tier };
  } catch {
    return null;
  }
}

export async function ssoLogout(): Promise<void> {
  try {
    const res = await api.post<{ logoutUrl: string }>("/auth/logout");
    if (res.data.logoutUrl) {
      window.location.href = res.data.logoutUrl;
      return;
    }
  } catch {
    // fall through — clearing the cookie failed server-side; reload anyway
  }
  window.location.reload();
}

export function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const sessionExpired = useAuthStore((s) => s.sessionExpired);
  const markAuthenticated = useAuthStore((s) => s.markAuthenticated);
  const requirePrompt = useAuthStore((s) => s.requirePrompt);
  const markDenied = useAuthStore((s) => s.markDenied);
  const setSsoEnabled = useAuthStore((s) => s.setSsoEnabled);
  const setApiKeyLogin = useAuthStore((s) => s.setApiKeyLogin);
  const ssoEnabled = useAuthStore((s) => s.ssoEnabled);
  const apiKeyLogin = useAuthStore((s) => s.apiKeyLogin);
  // The sign-in check failed with anything but a 404, so how this origin
  // lets people in is unknown.
  const [signInCheckFailed, setSignInCheckFailed] = useState(false);

  useEffect(() => {
    if (status !== "checking") return;
    let cancelled = false;

    async function probe() {
      // The tier of the stored key, as the sign-in check reports it. The
      // api client attaches the stored key to that call, so the answer is
      // about the key the /queue probe validates next. Undefined when the
      // check failed or an older backend did not report the field: the
      // store then treats the key as admin, as before #101.
      let keyTier: string | null | undefined;
      // 1. SSO session probe. 404 = older backend without the auth router.
      try {
        const check = await api.get<AuthCheck>("/auth/check", { suppressErrorToast: true });
        if (cancelled) return;
        setSsoEnabled(check.data.sso_enabled);
        setApiKeyLogin(check.data.api_key_login !== false);
        keyTier = check.data.key_tier;
        if (check.data.authenticated) {
          if (check.data.tier === "operator" || check.data.tier === "admin") {
            const user = await fetchSsoUser();
            if (!cancelled) markAuthenticated({ mode: "session", user });
            return;
          }
          const user = await fetchSsoUser();
          if (!cancelled) markDenied(user);
          return;
        }
      } catch (error: unknown) {
        // A 404 is an older backend without the auth router: the key form
        // stays. Any other failure leaves the origin's sign-in rules unknown,
        // and an SSO-only origin must not offer the key form, so a prompt
        // then says sign-in is unavailable. Either way the key probe runs:
        // a stored key that works still opens the app.
        if (!cancelled && !(isAxiosError(error) && error.response?.status === 404)) {
          setSignInCheckFailed(true);
        }
      }

      // 2. API-key probe (pre-SSO behavior, unchanged).
      try {
        await api.get(PROBE_PATH);
        if (!cancelled) markAuthenticated({ mode: "key", keyTier });
      } catch (error: unknown) {
        if (cancelled) return;
        if (isAxiosError(error) && error.response?.status === 401) {
          requirePrompt({ sessionExpired: false });
        } else {
          // Backend unreachable or erroring — let the app render and show
          // its own per-widget failure states.
          markAuthenticated({ mode: "key" });
        }
      }
    }

    void probe();
    return () => {
      cancelled = true;
    };
  }, [status, markAuthenticated, requirePrompt, markDenied, setSsoEnabled, setApiKeyLogin]);

  if (status === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
        <span className="text-sm">Connecting…</span>
      </div>
    );
  }

  if (status === "denied") {
    return <AccessDenied />;
  }

  if (status === "prompt") {
    if (ssoEnabled) {
      return <SsoRedirect />;
    }
    // An origin the server keeps SSO-only for people (api_key_login false)
    // never offers the key form, even while SSO reports off.
    if (!apiKeyLogin) {
      return <SignInUnavailable reason="sso-off" />;
    }
    if (signInCheckFailed) {
      return <SignInUnavailable reason="check-failed" />;
    }
    return <ApiKeyPrompt sessionExpired={sessionExpired} />;
  }

  return <>{children}</>;
}

function SsoRedirect() {
  useEffect(() => {
    window.location.assign("/api/v1/auth/login");
  }, []);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
      <span className="text-sm">Redirecting to sign-in…</span>
    </div>
  );
}

function SignInUnavailable({ reason }: { reason: "sso-off" | "check-failed" }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="rounded-lg border border-border bg-card w-full max-w-sm p-8 text-center">
        <h1 className="font-display text-xl font-semibold text-foreground">{APP_NAME}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {reason === "sso-off"
            ? "Sign-in is unavailable right now. This address signs people in with single sign-on, which the server reports is not configured. Try again later, or ask an administrator."
            : "Sign-in is unavailable right now: the server did not answer its sign-in check. Try again in a moment, or ask an administrator."}
        </p>
        <Button className="mt-6 w-full" variant="outline" onClick={() => window.location.reload()}>
          Try again
        </Button>
      </div>
    </div>
  );
}

function AccessDenied() {
  const user = useAuthStore((s) => s.user);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="rounded-lg border border-border bg-card w-full max-w-sm p-8 text-center">
        <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-destructive/20 bg-destructive/5">
          <ShieldX size={20} className="text-destructive" />
        </span>
        <h1 className="font-display text-xl font-semibold text-foreground">Access denied</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {user?.email ? (
            <>
              <span className="font-medium text-foreground">{user.email}</span> is signed in, but
              this account has no embry0 role.
            </>
          ) : (
            "Your account has no embry0 role."
          )}{" "}
          Ask an administrator to assign <code>embry0-operator</code> in Auth0.
        </p>
        <Button className="mt-6 w-full" variant="outline" onClick={() => void ssoLogout()}>
          Sign out
        </Button>
      </div>
    </div>
  );
}

function ApiKeyPrompt({ sessionExpired }: { sessionExpired: boolean }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  const markAuthenticated = useAuthStore((s) => s.markAuthenticated);
  const queryClient = useQueryClient();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const candidate = key.trim();
    if (!candidate || validating) return;
    setValidating(true);
    setError(null);
    try {
      await api.get(PROBE_PATH, {
        headers: { Authorization: `Bearer ${candidate}` },
      });
      const keyTier = await fetchKeyTier(candidate);
      setStoredApiKey(candidate);
      // Drop every cached 401 result so the app refetches with the new key.
      queryClient.clear();
      markAuthenticated({ mode: "key", keyTier });
    } catch (err: unknown) {
      if (isAxiosError(err) && err.response?.status === 401) {
        setError("Invalid API key — the server rejected it.");
      } else {
        setError("Could not reach the server to validate the key. Try again.");
      }
    } finally {
      setValidating(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="rounded-lg border border-border bg-card w-full max-w-sm p-8">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-primary-dim">
            <KeyRound size={20} className="text-primary" />
          </span>
          <div>
            <h1 className="font-display text-xl font-semibold text-foreground">
              {APP_NAME}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {sessionExpired
                ? "Your credentials were rejected — sign in again to continue."
                : "Sign in to access the dashboard."}
            </p>
          </div>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="password"
            autoFocus
            autoComplete="off"
            placeholder="API key"
            aria-label="API key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            disabled={validating}
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={!key.trim() || validating}
          >
            {validating ? "Validating…" : "Unlock"}
          </Button>
        </form>
        <p className="mt-4 text-center text-xs text-muted-foreground">
          The key is the orchestrator&apos;s <code>API_KEY</code>. It is stored
          only in this browser.
        </p>
      </div>
    </div>
  );
}
