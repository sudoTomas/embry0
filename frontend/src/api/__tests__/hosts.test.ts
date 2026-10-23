import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));

import { api } from "../client";
import { createHost, fetchHosts, importTailnetHosts, probeHost, removeHost, updateHost } from "../hosts";

beforeEach(() => vi.clearAllMocks());

describe("hosts api", () => {
  it("lists with the route's two filters, sending only the ones set", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { hosts: [], transports: [], sources: [], statuses: [], log_source_kinds: [] } });
    await fetchHosts({ source: undefined, includeRemoved: false });
    expect(api.get).toHaveBeenLastCalledWith("/hosts", { params: {} });
    await fetchHosts({ source: "tailnet", includeRemoved: true });
    expect(api.get).toHaveBeenLastCalledWith("/hosts", { params: { source: "tailnet", include_removed: true } });
  });

  it("writes with the contract's bodies and shows refusals itself", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { id: "host-1" } });
    (api.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { id: "host-1" } });
    (api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({});
    const body = { name: "web-1", address: "100.64.0.10", transport: "tailnet" as const, roles: ["app"], log_source: null, tags: [], repos: [{ repo: "acme/widgets", role: "app" }] };
    await createHost(body);
    expect(api.post).toHaveBeenCalledWith("/hosts", body, { suppressValidationToast: true });
    await updateHost("host-1", { status: "active" });
    expect(api.patch).toHaveBeenCalledWith("/hosts/host-1", { status: "active" }, { suppressValidationToast: true });
    await removeHost("host-1");
    expect(api.delete).toHaveBeenCalledWith("/hosts/host-1");
    await probeHost("host-1");
    expect(api.post).toHaveBeenLastCalledWith("/hosts/host-1/probe", {}, { suppressValidationToast: true });
    await importTailnetHosts();
    expect(api.post).toHaveBeenLastCalledWith("/hosts/import/tailnet", { confirm: true }, { suppressValidationToast: true });
  });
});
