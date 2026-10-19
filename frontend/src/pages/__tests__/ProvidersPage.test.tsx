import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchModelsCatalog = vi.fn();
const mockFetchProviderConfig = vi.fn();
const mockUpdateProviderConfig = vi.fn();
const mockTestCatalogProvider = vi.fn();

vi.mock("@/api/provider", () => ({
  fetchModelsCatalog: () => mockFetchModelsCatalog(),
  fetchProviderConfig: () => mockFetchProviderConfig(),
  updateProviderConfig: (c: unknown) => mockUpdateProviderConfig(c),
  testProviderConnection: vi.fn(),
  testCatalogProvider: (name: string) => mockTestCatalogProvider(name),
}));

vi.mock("@/stores/authStore", () => ({
  useAuthStore: (sel: (s: { authMode: string; user?: { tier: string } }) => unknown) =>
    sel({ authMode: "key", user: undefined }),
}));

import { ProvidersPage } from "../ProvidersPage";

const CATALOG = {
  heavy: "claude-opus-5",
  medium: "claude-sonnet-5",
  light: "claude-haiku-4-5",
  providers: [
    {
      name: "anthropic",
      base_url: "https://api.anthropic.com",
      available: true,
      credential_source: "oauth",
      default: true,
      models: [
        { model: "claude-opus-5", enabled: true, pricing_usd_per_mtok: { input: 5, output: 25 } },
        { model: "claude-sonnet-5", enabled: true, pricing_usd_per_mtok: { input: 3, output: 15 } },
        { model: "claude-haiku-4-5", enabled: true, pricing_usd_per_mtok: { input: 1, output: 5 } },
      ],
    },
    {
      name: "xai",
      base_url: "https://api.x.ai",
      available: false,
      credential_source: null,
      default: false,
      models: [
        { model: "grok-4.5", enabled: true, pricing_usd_per_mtok: { input: 2, output: 6 } },
        { model: "grok-4.7", enabled: true, pricing_usd_per_mtok: { input: 2, output: 6 } },
      ],
    },
  ],
};

const PROVIDER_CONFIG = {
  model_heavy: "claude-opus-5",
  model_medium: "claude-sonnet-5",
  model_light: "claude-haiku-4-5",
  default_model: "",
  disabled_models: [],
  api_key_set: false,
  oauth_token_set: true,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProvidersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchModelsCatalog.mockResolvedValue(CATALOG);
  mockFetchProviderConfig.mockResolvedValue(PROVIDER_CONFIG);
  mockUpdateProviderConfig.mockResolvedValue(PROVIDER_CONFIG);
});

describe("ProvidersPage", () => {
  it("shows each provider's last connection test", async () => {
    mockFetchModelsCatalog.mockResolvedValue({
      ...CATALOG,
      providers: [
        { ...CATALOG.providers[0], last_probe: { status: "unauthorized", checked_at: "2026-01-02T03:04:05+00:00" } },
        { ...CATALOG.providers[1], last_probe: null },
      ],
    });
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    const line = within(anthropic).getByTestId("provider-last-probe");
    expect(line).toHaveTextContent("Last test: Unauthorized");
    expect(line.querySelector("time")?.getAttribute("dateTime")).toBe("2026-01-02T03:04:05+00:00");
    const xai = screen.getByTestId("provider-card-xai");
    expect(within(xai).getByTestId("provider-last-probe")).toHaveTextContent("Last test: never");
  });

  it("shows no last-test line when the backend does not report one", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    expect(within(anthropic).queryByTestId("provider-last-probe")).not.toBeInTheDocument();
  });

  it("refreshes the catalog after a connection test so the last test updates", async () => {
    mockTestCatalogProvider.mockResolvedValue({ provider: "anthropic", status: "ok", checks: [] });
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    const before = mockFetchModelsCatalog.mock.calls.length;
    await userEvent.click(within(anthropic).getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(mockFetchModelsCatalog.mock.calls.length).toBeGreaterThan(before));
  });

  it("renders every catalog provider with availability and credential source", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    expect(within(anthropic).getByText("Available")).toBeInTheDocument();
    expect(within(anthropic).getByText("Default provider")).toBeInTheDocument();
    // The credential source reads in words; the identifier rides in the title.
    expect(within(anthropic).getByText("OAuth token")).toHaveAttribute("title", "oauth");
    const xai = screen.getByTestId("provider-card-xai");
    expect(within(xai).getByText("Unavailable")).toBeInTheDocument();
    expect(within(xai).getByText(/none configured/)).toBeInTheDocument();
  });

  it("titles each provider card with the provider's own spelling, not the stored id", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    expect(within(anthropic).getByText("Anthropic")).toBeInTheDocument();
    const xai = screen.getByTestId("provider-card-xai");
    const title = within(xai).getByText("xAI");
    expect(title.className).not.toContain("capitalize");
    expect(within(xai).queryByText("xai")).not.toBeInTheDocument();
  });

  it("shows model pricing in the provider table", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    const opusRow = within(anthropic).getByText("claude-opus-5").closest("tr")!;
    expect(within(opusRow).getByText("$5.00")).toBeInTheDocument();
    expect(within(opusRow).getByText("$25.00")).toBeInTheDocument();
  });

  it("runs a live connection test and renders per-source checks", async () => {
    mockTestCatalogProvider.mockResolvedValue({
      provider: "xai",
      status: "entitlement_refused",
      checks: [
        {
          source: "oauth_proxy",
          status: "entitlement_refused",
          message: "HTTP 403. xAI's 403 is ambiguous...",
        },
      ],
    });
    renderPage();
    const xai = await screen.findByTestId("provider-card-xai");
    await userEvent.click(within(xai).getByRole("button", { name: "Test connection" }));
    const results = await within(xai).findByTestId("probe-results");
    expect(within(results).getByText("Entitlement refused")).toBeInTheDocument();
    expect(mockTestCatalogProvider).toHaveBeenCalledWith("xai");
  });

  it("disabling a model sends the full disabled_models list", async () => {
    renderPage();
    const xai = await screen.findByTestId("provider-card-xai");
    const checkbox = within(xai).getByRole("checkbox", { name: "Enable grok-4.5" });
    expect(checkbox).toBeChecked();
    await userEvent.click(checkbox);
    await waitFor(() =>
      expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ disabled_models: ["grok-4.5"] }),
    );
  });

  it("tier-assigned models cannot be disabled", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    const checkbox = within(anthropic).getByRole("checkbox", { name: "Enable claude-opus-5" });
    expect(checkbox).toBeDisabled();
    expect(within(anthropic).getAllByText("(tier-assigned)").length).toBe(3);
  });

  it("saves tier reassignment through PUT /config/provider", async () => {
    renderPage();
    await screen.findByTestId("provider-card-anthropic");
    const heavySelect = screen.getByLabelText("Heavy");
    await userEvent.selectOptions(heavySelect, "claude-sonnet-5");
    await userEvent.click(screen.getByRole("button", { name: "Save tiers" }));
    await waitFor(() =>
      expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ model_heavy: "claude-sonnet-5" }),
    );
  });

  // #465: the default model is a real setting, not a stored-and-ignored field.
  it("says what the default model does", async () => {
    renderPage();
    await screen.findByTestId("provider-card-anthropic");
    const select = screen.getByLabelText("Default model");
    expect(select).toHaveAccessibleDescription(/every agent/i);
    expect(select).toHaveAccessibleDescription(/triage no longer picks models/i);
    expect(select).toHaveAccessibleDescription(/a job that names a model still wins/i);
  });

  it("saves the default model through PUT /config/provider", async () => {
    renderPage();
    await screen.findByTestId("provider-card-anthropic");
    await userEvent.selectOptions(screen.getByLabelText("Default model"), "grok-4.7");
    await userEvent.click(screen.getByRole("button", { name: "Save tiers" }));
    await waitFor(() =>
      expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ default_model: "grok-4.7" }),
    );
  });

  it("offers only enabled catalog models as the default model", async () => {
    mockFetchModelsCatalog.mockResolvedValue({
      ...CATALOG,
      providers: CATALOG.providers.map((p) =>
        p.name === "xai"
          ? {
              ...p,
              models: p.models.map((m) =>
                m.model === "grok-4.5" ? { ...m, enabled: false } : m,
              ),
            }
          : p,
      ),
    });
    renderPage();
    await screen.findByTestId("provider-card-anthropic");
    const options = within(screen.getByLabelText("Default model"))
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual([
      "",
      "claude-opus-5",
      "claude-sonnet-5",
      "claude-haiku-4-5",
      "grok-4.7",
    ]);
  });

  it("the default model cannot be disabled", async () => {
    mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, default_model: "grok-4.7" });
    renderPage();
    const xai = await screen.findByTestId("provider-card-xai");
    const locked = await within(xai).findByRole("checkbox", { name: "Enable grok-4.7" });
    await waitFor(() => expect(locked).toBeDisabled());
    expect(within(xai).getByText("(default model)")).toBeInTheDocument();
    expect(within(xai).getByRole("checkbox", { name: "Enable grok-4.5" })).toBeEnabled();
  });

  it("titles the page in sentence case, the name the sidebar and Settings use", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "LLM providers" })).toBeInTheDocument();
  });
});
