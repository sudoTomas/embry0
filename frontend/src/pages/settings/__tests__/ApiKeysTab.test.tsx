import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
const mockCreate = vi.fn();
const mockRevoke = vi.fn();
const mockSetRepos = vi.fn();
vi.mock("@/api/apiKeys", () => ({
  fetchApiKeys: (...a: unknown[]) => mockFetch(...a),
  createApiKey: (...a: unknown[]) => mockCreate(...a),
  revokeApiKey: (...a: unknown[]) => mockRevoke(...a),
  setApiKeyAllowedRepos: (...a: unknown[]) => mockSetRepos(...a),
}));

import ApiKeysTab from "../ApiKeysTab";
import { useAuthStore } from "@/stores/authStore";

const KEY = {
  id: "key-1",
  user_id: "usr-1",
  name: "laptop-cli",
  prefix: "emb_abcd1234",
  scope: "operate" as const,
  service: false,
  created_by: "op@example.com",
  created_at: "2026-08-03T12:00:00Z",
  expires_at: null,
  last_used_at: null,
  revoked_at: null,
  allowed_repos: null as string[] | null,
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ApiKeysTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetch.mockReset();
  mockCreate.mockReset();
  mockRevoke.mockReset();
  mockSetRepos.mockReset();
  useAuthStore.setState({
    status: "authenticated",
    sessionExpired: false,
    authMode: "session",
    user: { email: "op@example.com", name: "Op", tier: "operator" },
    keyTier: undefined,
    ssoEnabled: true,
    apiKeyLogin: true,
  });
});

describe("ApiKeysTab", () => {
  it("lists keys with scope and status", async () => {
    mockFetch.mockResolvedValue([KEY]);
    renderTab();
    const row = (await screen.findByText("laptop-cli")).closest("tr")!;
    expect(within(row).getByText("operate")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
  });

  it("caps the scope select at the operator tier", async () => {
    mockFetch.mockResolvedValue([]);
    renderTab();
    await screen.findByText(/no api keys/i);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["read", "operate"]);
  });

  it("caps the scope select at the operator tier for a key session of tier operator (#101)", async () => {
    useAuthStore.setState({ authMode: "key", user: null, keyTier: "operator" });
    mockFetch.mockResolvedValue([]);
    renderTab();
    await screen.findByText(/no api keys/i);
    expect(
      screen.getAllByRole("option").map((o) => o.textContent),
      "an operate-scope key cannot mint admin or merge keys",
    ).toEqual(["read", "operate"]);
    expect(screen.queryByText("Service key (unowned — for agents/infra such as a chat relay)")).toBeNull();
  });

  it("offers admin scope and the service checkbox to admins", async () => {
    useAuthStore.setState({ user: { email: "a@example.com", name: "A", tier: "admin" } });
    mockFetch.mockResolvedValue([]);
    renderTab();
    await screen.findByText(/no api keys/i);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toContain("admin");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toContain("merge");
    expect(screen.getByText("Service key (unowned — for agents/infra such as a chat relay)")).toBeInTheDocument();
  });

  it("shows the minted key exactly once after create", async () => {
    mockFetch.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...KEY, key: "emb_SHOWN-ONCE-TOKEN" });
    renderTab();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "laptop-cli");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(await screen.findByTestId("minted-key")).toHaveTextContent("emb_SHOWN-ONCE-TOKEN");
  });

  it("shows the Claude Code connection for this origin, the key in no command", async () => {
    mockFetch.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...KEY, key: "emb_SHOWN-ONCE-TOKEN" });
    renderTab();
    const user = userEvent.setup();
    // After setup, which puts its own clipboard in place.
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.type(await screen.findByLabelText("Name"), "laptop-cli");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    // The key is read at a prompt that shows nothing, then written to a file only its owner reads; the server's
    // entry holds a headersHelper that reads the file: no command takes the key, no setting holds it.
    expect((await screen.findByTestId("mcp-command-key")).textContent).toBe(
      `EMBRY0_URL=${window.location.origin}\nread -rs EMBRY0_API_KEY`,
    );
    expect(screen.getByTestId("mcp-command").textContent).toBe(
      [
        "mkdir -p ~/.config/embry0",
        "chmod 700 ~/.config/embry0",
        `[ -n "$EMBRY0_API_KEY" ] && rm -f ~/.config/embry0/api-key && (umask 077; printf '%s' "$EMBRY0_API_KEY" > ~/.config/embry0/api-key)`,
        "unset EMBRY0_API_KEY",
        `HELPER="jq -Rs '{Authorization: (\\"Bearer \\" + .)}' ~/.config/embry0/api-key"`,
        "claude mcp remove --scope user embry0 2>/dev/null",
        `claude mcp add-json --scope user embry0 "$(jq -n --arg url "$EMBRY0_URL/mcp" --arg helper "$HELPER" \\`,
        `  '{type: "http", url: $url, headersHelper: $helper}')"`,
        "claude mcp list",
      ].join("\n"),
    );
    expect(screen.getByTestId("mcp-old-command")).toHaveTextContent(
      "Connected before with a claude mcp add command that carried the key? Revoke that key below",
    );
    const shown = document.body.textContent ?? "";
    expect(shown.split("emb_SHOWN-ONCE-TOKEN").length - 1).toBe(1);
    expect(screen.getByTestId("minted-key")).toHaveTextContent("emb_SHOWN-ONCE-TOKEN");
    for (const button of screen.getAllByRole("button", { name: "Copy the commands" })) {
      await user.click(button);
    }
    expect(writeText).toHaveBeenCalledTimes(2);
    for (const [copied] of writeText.mock.calls) {
      expect(copied).not.toContain("emb_SHOWN-ONCE-TOKEN");
    }
  });

  it("revokes with confirmation", async () => {
    mockFetch.mockResolvedValue([KEY]);
    mockRevoke.mockResolvedValue({ ...KEY, revoked_at: "2026-08-03T13:00:00Z" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderTab();
    const user = userEvent.setup();
    await user.click(await screen.findByTitle(/revoke this key/i));
    expect(mockRevoke).toHaveBeenCalledWith("key-1");
  });

  it("shows each key's repositories, or all when unrestricted", async () => {
    mockFetch.mockResolvedValue([KEY, { ...KEY, id: "key-2", name: "bot", allowed_repos: ["acme/widgets", "acme/docs"] }]);
    renderTab();
    expect(await screen.findByTestId("key-repos-key-1")).toHaveTextContent("all");
    expect(screen.getByTestId("key-repos-key-2")).toHaveTextContent("acme/widgets, acme/docs");
  });

  it("mints a key limited to the repositories typed", async () => {
    mockFetch.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...KEY, allowed_repos: ["acme/widgets", "acme/docs"], key: "emb_T" });
    renderTab();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "bot");
    await user.type(screen.getByLabelText("Repositories (optional)"), "acme/widgets, acme/docs");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(mockCreate.mock.calls[0][0]).toMatchObject({ allowed_repos: ["acme/widgets", "acme/docs"] });
  });

  it("mints an unrestricted key when no repository is typed", async () => {
    mockFetch.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...KEY, key: "emb_T" });
    renderTab();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "cli");
    await user.click(screen.getByRole("button", { name: /create key/i }));
    expect(mockCreate.mock.calls[0][0].allowed_repos).toBeUndefined();
  });

  it("changes a key's repositories, and an empty answer lifts the limit", async () => {
    mockFetch.mockResolvedValue([{ ...KEY, allowed_repos: ["acme/widgets"] }]);
    mockSetRepos.mockResolvedValue({ ...KEY, allowed_repos: null });
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("");
    renderTab();
    const user = userEvent.setup();
    await user.click(await screen.findByTitle(/change the repositories/i));
    expect(prompt.mock.calls[0][1]).toBe("acme/widgets");
    expect(mockSetRepos).toHaveBeenCalledWith("key-1", []);
  });
});
