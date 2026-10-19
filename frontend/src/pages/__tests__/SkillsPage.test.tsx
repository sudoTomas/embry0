import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";

const sources = [
  { id: "superpowers", kind: "git", url: "https://github.com/obra/superpowers.git", ref: "v6.4.2", resolved_sha: "8ca22dba9a94f28898bbce59f2537ff4d87c747d", plugin_name: "superpowers", skills: [{ name: "writing-plans", description: "" }], status: "ready", error: null, enabled: true, is_builtin: true, synced_at: "2026-10-19T00:00:00Z" },
  { id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "main", resolved_sha: null, plugin_name: null, skills: [], status: "error", error: "git timed out after 120s", enabled: true, is_builtin: false, synced_at: null },
  { id: "local", kind: "inline", url: null, ref: null, resolved_sha: "da39a3ee", plugin_name: "local", skills: [], status: "ready", error: null, enabled: true, is_builtin: true, synced_at: null },
];
const catalog = [
  { name: "superpowers:writing-plans", source_id: "superpowers", description: "Plans", available: true, status: "ready" },
  { name: "team:deploy", source_id: "team", description: "", available: false, status: "error" },
];
const inline = [{ name: "release-notes", description: "Write release notes", body: "# Release notes", created_at: null, updated_at: "2026-10-19T00:00:00Z" }];
const mockCreate = vi.fn(); const mockSync = vi.fn(); const mockDelete = vi.fn(); const mockUpdate = vi.fn(); const mockPut = vi.fn(); const mockDeleteInline = vi.fn();
vi.mock("@/hooks/useSkills", () => ({
  useSkillSources: () => ({ data: sources, isLoading: false, isError: false }),
  useSkills: () => ({ data: catalog, isLoading: false }),
  useInlineSkills: () => ({ data: inline, isLoading: false }),
  useCreateSkillSource: () => ({ mutateAsync: mockCreate, isPending: false }),
  useUpdateSkillSource: () => ({ mutate: mockUpdate, isPending: false }),
  useSyncSkillSource: () => ({ mutate: mockSync, isPending: false }),
  useDeleteSkillSource: () => ({ mutateAsync: mockDelete, isPending: false }),
  usePutInlineSkill: () => ({ mutateAsync: mockPut, isPending: false }),
  useDeleteInlineSkill: () => ({ mutateAsync: mockDeleteInline, isPending: false }),
  anySourceSyncing: () => false,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
import { SkillsPage } from "../SkillsPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><SkillsPage /></MemoryRouter></QueryClientProvider>);
}
const asTier = (tier: string) => useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "u@example.com", name: "U", tier }, ssoEnabled: true, apiKeyLogin: true });

describe("SkillsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); asTier("admin"); });

  it("lists sources with status, sha, count and the error inline", () => {
    renderPage();
    const row = screen.getByRole("row", { name: "superpowers" });
    expect(row).toHaveTextContent("ready");
    expect(row).toHaveTextContent("8ca22dba9a94");
    expect(row).toHaveTextContent("1 skill");
    const broken = screen.getByRole("row", { name: "team" });
    expect(broken).toHaveTextContent("error");
    expect(broken).toHaveTextContent("git timed out after 120s");
    expect(within(broken).getByRole("button", { name: /sync/i })).toBeEnabled();
  });
  it("refuses Remove on a builtin and asks for it on a custom source", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(within(screen.getByRole("row", { name: "superpowers" })).getByRole("button", { name: /remove/i })).toBeDisabled();
    mockDelete.mockRejectedValueOnce({ response: { status: 409, data: { detail: "referenced", agents: ["developer"], templates: ["quick-fix"] } } });
    await user.click(within(screen.getByRole("row", { name: "team" })).getByRole("button", { name: /remove/i }));
    expect(mockDelete).toHaveBeenCalledWith("team");
    expect(await screen.findByRole("alert")).toHaveTextContent(/developer/);
    expect(screen.getByRole("alert")).toHaveTextContent(/quick-fix/);
  });
  it("adds a source and shows a 422 detail", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/^id$/i), "acme");
    await user.type(screen.getByLabelText(/^url$/i), "http://example.com/x.git");
    await user.type(screen.getByLabelText(/^ref$/i), "v1");
    mockCreate.mockRejectedValueOnce({ response: { status: 422, data: { detail: "url must be https://" } } });
    await user.click(screen.getByRole("button", { name: /add source/i }));
    expect(mockCreate).toHaveBeenCalledWith({ id: "acme", kind: "git", url: "http://example.com/x.git", ref: "v1", enabled: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("url must be https://");
  });
  it("toggles enabled and syncs", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = screen.getByRole("row", { name: "team" });
    await user.click(within(row).getByRole("checkbox", { name: /enabled/i }));
    expect(mockUpdate).toHaveBeenCalledWith({ id: "team", body: { enabled: false } });
    await user.click(within(row).getByRole("button", { name: /sync/i }));
    expect(mockSync).toHaveBeenCalledWith("team");
  });
  it("edits an inline skill and saves it", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /release-notes/ }));
    expect(screen.getByLabelText(/^name$/i)).toHaveValue("release-notes");
    expect(screen.getByLabelText(/^name$/i)).toBeDisabled();
    await user.clear(screen.getByLabelText(/^body$/i));
    await user.type(screen.getByLabelText(/^body$/i), "# New body");
    await user.click(screen.getByRole("button", { name: /save skill/i }));
    expect(mockPut).toHaveBeenCalledWith({ name: "release-notes", body: { description: "Write release notes", body: "# New body" } });
  });
  it("shows the catalog with availability", () => {
    renderPage();
    const table = screen.getByRole("table", { name: /catalog/i });
    expect(within(table).getByRole("row", { name: /team:deploy/ })).toHaveTextContent("error");
    expect(within(table).getByRole("row", { name: /superpowers:writing-plans/ })).toHaveTextContent("available");
  });
  it("is read-only for the operator tier", () => {
    asTier("operator");
    renderPage();
    expect(screen.getByRole("button", { name: /add source/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save skill/i })).toBeDisabled();
    for (const b of screen.getAllByRole("button", { name: /sync/i })) expect(b).toBeDisabled();
  });
});
