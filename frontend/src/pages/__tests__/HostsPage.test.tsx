import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { useAuthStore } from "@/stores/authStore";

const mockUseHosts = vi.fn();
const probe = vi.fn();
const remove = vi.fn();
const update = vi.fn();
const importHosts = vi.fn();
vi.mock("@/hooks/useHosts", () => ({
  useHosts: (filter: unknown) => mockUseHosts(filter),
  useProbeHost: () => ({ mutate: probe, isPending: false }),
  useRemoveHost: () => ({ mutate: remove, isPending: false }),
  useUpdateHost: () => ({ mutate: update, isPending: false }),
  useCreateHost: () => ({ mutate: vi.fn(), isPending: false }),
  useImportTailnetHosts: () => ({ mutate: importHosts, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { HostsPage } from "../HostsPage";

const WEB = {
  id: "host-1", name: "web-1", address: "100.64.0.10", transport: "tailnet", roles: ["app"], log_source: null, tags: ["tag:server"],
  source: "tailnet", last_seen_at: "2026-10-16T11:59:00Z", status: "active", created_at: "2026-10-16T12:00:00Z", updated_at: "2026-10-16T12:00:00Z", repos: [],
};
const DB = { ...WEB, id: "host-2", name: "db-1", address: "100.64.0.11", roles: ["db"], source: "manual", status: "unreachable", last_seen_at: null };
const LIST = { hosts: [WEB, DB], transports: ["tailnet", "ssh", "http"], sources: ["manual", "tailnet"], statuses: ["active", "unreachable", "removed"], log_source_kinds: ["journald", "docker", "loki"] };

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <HostsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  [probe, remove, update, importHosts].forEach((m) => m.mockReset());
  mockUseHosts.mockReset().mockReturnValue(ready(LIST));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("HostsPage", () => {
  it("lists each host with its address, transport, roles, status and last-seen time", () => {
    renderPage();
    const rows = screen.getAllByTestId("host-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("web-1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("100.64.0.10")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Active")).toBeInTheDocument();
    // The transport column, by position: the source column reads "Tailnet" too for this row.
    expect(within(rows[0]).getAllByRole("cell")[2]).toHaveTextContent(/^Tailnet$/);
    expect(within(rows[1]).getByText("Unreachable")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Never")).toBeInTheDocument();
  });

  it("filters by source and shows removed hosts on request, through the hook", async () => {
    renderPage();
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: undefined, includeRemoved: false });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sources" }), "manual");
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: "manual", includeRemoved: false });
    await userEvent.click(screen.getByRole("switch", { name: "Show removed" }));
    expect(mockUseHosts).toHaveBeenLastCalledWith({ source: "manual", includeRemoved: true });
  });

  it("probes a host from its menu and says what came back", async () => {
    probe.mockImplementation((_id: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ probe: { reachable: false, detail: "connect timed out" }, host: DB }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for db-1" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Probe" }));
    expect(probe).toHaveBeenCalledWith("host-2", expect.anything());
    expect(toast.error).toHaveBeenCalledWith("db-1 is unreachable: connect timed out");
  });

  it("removes a host behind a confirm", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", { name: "Remove web-1?" });
    expect(dialog).toHaveTextContent(/repository links are dropped/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith("host-1", expect.anything());
  });

  it("restores a removed host from its menu, where Probe and Edit are not offered", async () => {
    mockUseHosts.mockReturnValue(ready({ ...LIST, hosts: [{ ...WEB, status: "removed" }] }));
    update.mockImplementation((_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess());
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    expect(screen.getByRole("menuitem", { name: "Probe" })).toBeDisabled();
    // A removed host cannot be edited: the route refuses repository links until it is restored.
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore" }));
    expect(update).toHaveBeenCalledWith({ id: "host-1", patch: { status: "active" } }, expect.anything());
    expect(toast.success).toHaveBeenCalledWith("Restored web-1");
  });

  it("imports from the tailnet behind a confirm and reports the counts", async () => {
    importHosts.mockImplementation((_v: unknown, opts: { onSuccess: (r: unknown) => void }) =>
      opts.onSuccess({ tailnet: "example.ts.net", credential_id: null, results: [], counts: { created: 1, updated: 2, unchanged: 3, skipped: 0 } }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Import from tailnet" }));
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(importHosts).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith("Imported from example.ts.net: 1 created, 2 updated, 3 unchanged, 0 skipped");
  });

  it("opens the add form from the page's one gold button", async () => {
    renderPage();
    const add = screen.getByRole("button", { name: "Add host" });
    expect(add.className).toContain("bg-primary");
    await userEvent.click(add);
    expect(screen.getByRole("dialog", { name: "Add host" })).toBeInTheDocument();
  });

  it("offers the admin actions disabled to an operator session", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    renderPage();
    expect(screen.getByRole("button", { name: "Add host" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import from tailnet" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Actions for web-1" }));
    expect(screen.getByRole("menuitem", { name: "Probe" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Remove" })).toBeDisabled();
  });

  it("shows honest empty and error states", () => {
    mockUseHosts.mockReturnValue(ready({ ...LIST, hosts: [] }));
    renderPage();
    expect(screen.getByText("No hosts yet")).toBeInTheDocument();
    mockUseHosts.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText("Failed to load hosts")).toBeInTheDocument();
  });
});
