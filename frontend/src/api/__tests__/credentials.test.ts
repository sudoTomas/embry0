import { describe, expect, it, vi, beforeEach } from "vitest";
import type { AxiosRequestConfig } from "axios";

describe("credentials api", () => {
  beforeEach(() => vi.resetModules());

  async function capture(data: unknown = {}) {
    const { api } = await import("../client");
    const captured: { config?: AxiosRequestConfig } = {};
    api.defaults.adapter = async (config) => { captured.config = config; return { data, status: 200, statusText: "OK", headers: {}, config }; };
    return captured;
  }

  it("creates without echoing the secret back into the returned object", async () => {
    const captured = await capture({ id: "cred-000000000001", label: "alice", masked_preview: "ghp_••••••••mnop" });
    const { createCredential } = await import("../credentials");
    const out = await createCredential({ kind: "forge_token", provider: "github", label: "alice", secret: "ghp_abcdefghijklmnop" });
    expect(captured.config?.url).toBe("/credentials");
    expect(captured.config?.method).toBe("post");
    expect(JSON.parse(captured.config?.data as string).secret).toBe("ghp_abcdefghijklmnop");
    expect(captured.config?.suppressValidationToast).toBe(true);
    expect(JSON.stringify(out)).not.toContain("ghp_abcdefghijklmnop");
  });

  it("resolve passes repo and role as params", async () => {
    const captured = await capture({ id: null, label: "GITHUB_TOKEN", provider: "github", host: "github.com", source: "env", trace: [] });
    const { resolveCredential } = await import("../credentials");
    await resolveCredential("acme/widgets");
    expect(captured.config?.url).toBe("/credentials/resolve");
    expect(captured.config?.params).toEqual({ repo: "acme/widgets", role: "forge" });
  });

  it("scoped bindings PUT suppresses the global 422 toast", async () => {
    const captured = await capture({ bindings: [] });
    const { setScopedBindings } = await import("../credentials");
    await setScopedBindings("owner", "github:acme", [{ credential_id: "cred-000000000001", role: "forge" }]);
    expect(captured.config?.url).toBe("/credentials/bindings");
    expect(captured.config?.method).toBe("put");
    expect(JSON.parse(captured.config?.data as string)).toEqual({
      scope_kind: "owner",
      scope_value: "github:acme",
      bindings: [{ credential_id: "cred-000000000001", role: "forge" }],
    });
    expect(captured.config?.suppressValidationToast).toBe(true);
  });

  it("starts and polls an xAI device login without sending a client id", async () => {
    const captured = await capture({
      login_id: "login-1",
      user_code: "ABCD-EFGH",
      verification_uri_complete: "https://x.ai/device?user_code=ABCD-EFGH",
      expires_at: "2026-10-01T00:05:00Z",
      interval: 5,
    });
    const { startXaiDeviceLogin, pollXaiDeviceLogin } = await import("../credentials");
    const started = await startXaiDeviceLogin("grok-default");
    expect(captured.config?.url).toBe("/credentials/xai/device-login");
    expect(captured.config?.method).toBe("post");
    expect(JSON.parse(captured.config?.data as string)).toEqual({ label: "grok-default" });
    expect(started.interval).toBe(5);
    expect(started.user_code).toBe("ABCD-EFGH");

    const polled = await capture({ status: "pending" });
    await pollXaiDeviceLogin("login-1");
    expect(polled.config?.url).toBe("/credentials/xai/device-login/login-1");
    expect(polled.config?.method).toBe("get");
  });

  it("repo bindings PUT sends the bindings array unencoded path", async () => {
    const captured = await capture({ repo: "acme/widgets", bindings: [], effective: {} });
    const { setRepoCredentials } = await import("../credentials");
    await setRepoCredentials("acme/widgets", [{ credential_id: "cred-000000000001", role: "forge" }]);
    expect(captured.config?.url).toBe("/repos/acme/widgets/-/credentials");
    expect(JSON.parse(captured.config?.data as string)).toEqual({ bindings: [{ credential_id: "cred-000000000001", role: "forge" }] });
  });
});
