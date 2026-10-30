import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchModelsCatalog = vi.fn();
const mockFetchProviderConfig = vi.fn();
const mockUpdateProviderConfig = vi.fn();
const mockTestCatalogProvider = vi.fn();
const mockFetchProviderUsage = vi.fn();
const mockAddProviderKey = vi.fn();

vi.mock("@/api/provider", () => ({
  fetchModelsCatalog: () => mockFetchModelsCatalog(),
  fetchProviderConfig: () => mockFetchProviderConfig(),
  updateProviderConfig: (c: unknown) => mockUpdateProviderConfig(c),
  testProviderConnection: vi.fn(),
  testCatalogProvider: (name: string) => mockTestCatalogProvider(name),
  fetchProviderUsage: (name: string) => mockFetchProviderUsage(name),
}));

vi.mock("@/api/credentials", () => ({
  addProviderKey: (provider: string, body: unknown) => mockAddProviderKey(provider, body),
}));

const mockFetchEffectiveSettings = vi.fn();
const mockFetchSettings = vi.fn();
const mockSetSettingsKeys = vi.fn();

// The background models card reads and writes the settings document (#164).
vi.mock("@/api/settings", () => ({
  fetchEffectiveSettings: () => mockFetchEffectiveSettings(),
  fetchSettings: () => mockFetchSettings(),
  setSettingsKeys: (request: unknown, layer: unknown) => mockSetSettingsKeys(request, layer),
}));

// An admin session unless a test says otherwise: the page reads only useIsAdmin (#101).
let mockIsAdmin = true;
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => mockIsAdmin,
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

/** One key of `background_models` as `GET /config/settings/effective` answers it. */
function backgroundKey(key: string, value: unknown, extra: Record<string, unknown> = {}) {
  return {
    key,
    value,
    default: null,
    source: value === null ? "default" : "document",
    set_by: null,
    set_at: null,
    env: null,
    env_still_set: false,
    overridable: ["organization"],
    refused: null,
    description: null,
    ...extra,
  };
}

/** Each key's default, as the section declares it: the scout is off until a model is named for it. */
const BACKGROUND_DEFAULTS: Record<string, unknown> = { scout: false };

function effectiveSettings(values: Record<string, unknown>, extra: Record<string, Record<string, unknown>> = {}) {
  const keys = ["default", "watcher", "context_audit", "scout", "proposal_brief", "job_brief", "slop_judge"];
  return {
    generation: 7,
    sections: [
      {
        name: "background_models",
        summary: "",
        keys: keys.map((key) => {
          const fallback = BACKGROUND_DEFAULTS[key] ?? null;
          const value = key in values ? values[key] : fallback;
          const source = key in values ? "document" : "default";
          return backgroundKey(key, value, { default: fallback, source, ...extra[key] });
        }),
      },
    ],
    unknown_sections: [],
    unknown_keys: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockIsAdmin = true;
  mockFetchEffectiveSettings.mockResolvedValue(effectiveSettings({}));
  mockFetchSettings.mockResolvedValue({ allowlist_covers: true });
  mockSetSettingsKeys.mockResolvedValue({ generation: 8, revision_id: 3, changes: [], managed_by: null });
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

  it("wraps a provider's base URL inside its card on a phone", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    // A URL has no spaces, so without a break anywhere a long one pushed
    // main sideways on a phone screen.
    expect(within(anthropic).getByText("https://api.anthropic.com")).toHaveClass("[overflow-wrap:anywhere]");
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

  it("marks a model its provider serves without tool calling", async () => {
    mockFetchModelsCatalog.mockResolvedValue({
      ...CATALOG,
      providers: [
        ...CATALOG.providers,
        {
          name: "openai",
          base_url: "https://api.openai.com/v1",
          available: false,
          credential_source: null,
          default: false,
          models: [
            { model: "gpt-6.1-sol", enabled: true, tool_calling: false, pricing_usd_per_mtok: { input: 2, output: 10 } },
            { model: "gpt-6-luna", enabled: true, tool_calling: true, pricing_usd_per_mtok: { input: 0.1, output: 0.5 } },
          ],
        },
      ],
    });
    renderPage();
    const openai = await screen.findByTestId("provider-card-openai");
    const chatOnly = within(openai).getByText("gpt-6.1-sol").closest("tr")!;
    expect(within(chatOnly).getByText("(chat-only)")).toBeInTheDocument();
    const withTools = within(openai).getByText("gpt-6-luna").closest("tr")!;
    expect(within(withTools).queryByText("(chat-only)")).not.toBeInTheDocument();
    const anthropic = screen.getByTestId("provider-card-anthropic");
    expect(within(anthropic).queryByText("(chat-only)")).not.toBeInTheDocument();
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

describe("ProvidersPage keys and spend", () => {
  const OPENROUTER = {
    name: "openrouter",
    base_url: "https://openrouter.ai/api/v1",
    available: false,
    credential_source: null,
    default: false,
    models: [{ model: "openrouter/openai/gpt-6.1-sol", enabled: true, pricing_usd_per_mtok: { input: 2, output: 10 } }],
  };

  it("offers Add key on a card without one and Replace key on a card with one", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-card-anthropic");
    expect(within(anthropic).getByRole("button", { name: "Replace key" })).toBeInTheDocument();
    expect(within(screen.getByTestId("provider-card-xai")).getByRole("button", { name: "Add key" })).toBeInTheDocument();
  });

  it("saves a key from the card and makes it the provider's global key", async () => {
    mockFetchModelsCatalog.mockResolvedValue({ ...CATALOG, providers: [...CATALOG.providers, OPENROUTER] });
    mockAddProviderKey.mockResolvedValue({ id: "cred-1" });
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    await userEvent.click(within(card).getByRole("button", { name: "Add key" }));
    await userEvent.type(screen.getByLabelText("API key"), "sk-or-test");
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(mockAddProviderKey).toHaveBeenCalled());
    expect(mockAddProviderKey).toHaveBeenCalledWith("openrouter", { secret: "sk-or-test", label: "api-key" });
  });

  it("shows what OpenRouter says the key has spent, its limit and the account's credit", async () => {
    mockFetchModelsCatalog.mockResolvedValue({
      ...CATALOG,
      providers: [...CATALOG.providers, { ...OPENROUTER, available: true, credential_source: "registry" }],
    });
    mockFetchProviderUsage.mockResolvedValue({
      provider: "openrouter",
      status: "ok",
      checked_at: "2026-10-25T00:00:00+00:00",
      key: {
        spend_today_usd: 0.25,
        spend_week_usd: 3,
        spend_month_usd: 9.75,
        spend_total_usd: 12.5,
        limit_usd: 50,
        limit_remaining_usd: 47,
        limit_reset: "weekly",
        limit_resets_at: "2026-10-26T00:00:00.000Z",
        expires_at: null,
      },
      account: { credits_usd: 275, used_usd: 134.86, remaining_usd: 140.14 },
    });
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    const spend = await within(card).findByTestId("provider-spend");
    expect(spend).toHaveTextContent("Spent $0.25 today · $3.00 this week · $9.75 this month · $12.50 in all");
    expect(spend).toHaveTextContent("Key limit $50.00 weekly, $47.00 left");
    expect(spend).toHaveTextContent("Account credit $140.14 left of $275.00");
    expect(mockFetchProviderUsage).toHaveBeenCalledWith("openrouter");
    expect(within(screen.getByTestId("provider-card-anthropic")).queryByTestId("provider-spend")).toBeNull();
  });

  describe("fallback order", () => {
    it("is off until a model is added, and saves the order it shows", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, fallback_models: [] });
      renderPage();
      const card = await screen.findByTestId("fallback-order");
      expect(card).toHaveTextContent("No fallback: off.");
      const save = within(card).getByRole("button", { name: "Save order" });
      expect(save).toBeDisabled();

      await userEvent.selectOptions(within(card).getByLabelText("Add a model"), "grok-4.7");
      await userEvent.click(within(card).getByRole("button", { name: "Add" }));
      await userEvent.selectOptions(within(card).getByLabelText("Add a model"), "claude-haiku-4-5");
      await userEvent.click(within(card).getByRole("button", { name: "Add" }));
      await userEvent.click(within(card).getByRole("button", { name: "Move claude-haiku-4-5 up" }));
      await userEvent.click(save);

      await waitFor(() =>
        expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ fallback_models: ["claude-haiku-4-5", "grok-4.7"] }),
      );
    });

    it("locks a fallback model's enablement and can turn the order off", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, fallback_models: ["grok-4.7"] });
      renderPage();
      const card = await screen.findByTestId("fallback-order");
      const xai = screen.getByTestId("provider-card-xai");
      expect(within(xai).getByLabelText("Enable grok-4.7")).toBeDisabled();
      expect(within(xai).getByLabelText("Enable grok-4.5")).toBeEnabled();

      await userEvent.click(within(card).getByRole("button", { name: "Remove grok-4.7" }));
      await userEvent.click(within(card).getByRole("button", { name: "Save order" }));
      await waitFor(() => expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ fallback_models: [] }));
    });

    it("is hidden against a backend without the setting", async () => {
      renderPage();
      await screen.findByTestId("provider-card-anthropic");
      expect(screen.queryByTestId("fallback-order")).toBeNull();
    });

    it("offers the local model, which no catalogue lists, to end the order", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, fallback_models: ["grok-4.7"], local_max_runs: 1 });
      // A model of the operator's own server is named by the background models (#164).
      mockFetchEffectiveSettings.mockResolvedValue(effectiveSettings({ default: "custom/qwen" }));
      renderPage();
      const card = await screen.findByTestId("fallback-order");
      await userEvent.selectOptions(within(card).getByLabelText("Add a model"), "custom/qwen");
      await userEvent.click(within(card).getByRole("button", { name: "Add" }));
      await userEvent.click(within(card).getByRole("button", { name: "Save order" }));
      await waitFor(() =>
        expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ fallback_models: ["grok-4.7", "custom/qwen"] }),
      );
    });
  });

  describe("local model", () => {
    it("saves how many runs at once, and nothing about a model", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, local_max_runs: 1 });
      renderPage();
      const card = await screen.findByTestId("local-model");
      const save = within(card).getByRole("button", { name: "Save" });
      expect(save).toBeDisabled();

      const runs = within(card).getByLabelText("Runs at once");
      await userEvent.clear(runs);
      await userEvent.type(runs, "2");
      await userEvent.click(save);
      await waitFor(() => expect(mockUpdateProviderConfig).toHaveBeenCalledWith({ local_max_runs: 2 }));
    });

    it("will not save a cap out of range", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, local_max_runs: 1 });
      renderPage();
      const card = await screen.findByTestId("local-model");
      const runs = within(card).getByLabelText("Runs at once");
      await userEvent.clear(runs);
      await userEvent.type(runs, "65");
      expect(within(card).getByText("Runs at once is a whole number from 1 to 64.")).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Save" })).toBeDisabled();
    });

    it("is hidden against a backend without the setting", async () => {
      renderPage();
      await screen.findByTestId("provider-card-anthropic");
      expect(screen.queryByTestId("local-model")).toBeNull();
    });
  });

  describe("background models (#164)", () => {
    const WRITTEN = { section: "background_models", generation: 7 };

    it("writes the default with a change of that one key, never through the provider settings", async () => {
      renderPage();
      const card = await screen.findByTestId("background-models");
      const field = within(card).getByLabelText("Background work runs on");
      await userEvent.type(field, "custom/qwen");
      await userEvent.click(within(card).getByRole("button", { name: "Save" }));
      await waitFor(() =>
        expect(mockSetSettingsKeys).toHaveBeenCalledWith(
          { ...WRITTEN, values: { default: "custom/qwen" } },
          { layer: "deployment" },
        ),
      );
      expect(mockUpdateProviderConfig).not.toHaveBeenCalled();
    });

    it("sends null to take the default out when the field is emptied", async () => {
      mockFetchEffectiveSettings.mockResolvedValue(effectiveSettings({ default: "custom/qwen" }));
      renderPage();
      const card = await screen.findByTestId("background-models");
      const field = within(card).getByLabelText("Background work runs on");
      expect(field).toHaveValue("custom/qwen");
      await userEvent.clear(field);
      await userEvent.click(within(card).getByRole("button", { name: "Save" }));
      await waitFor(() =>
        expect(mockSetSettingsKeys).toHaveBeenCalledWith({ ...WRITTEN, values: { default: null } }, { layer: "deployment" }),
      );
    });

    it("shows where the value comes from and what each feature runs on", async () => {
      mockFetchEffectiveSettings.mockResolvedValue(
        effectiveSettings(
          { default: "custom/qwen", scout: false, proposal_brief: "gpt-6-luna" },
          { default: { set_by: "alex@example.com", set_at: "2026-10-30T09:00:00+00:00" } },
        ),
      );
      renderPage();
      const card = await screen.findByTestId("background-models");
      expect(within(card).getByTestId("background-model-source")).toHaveTextContent(
        /Set in the document · set by alex@example\.com on/,
      );
      expect(within(card).getByTestId("background-model-watcher")).toHaveTextContent("custom/qwen(the default)");
      expect(within(card).getByTestId("background-model-scout")).toHaveTextContent("no model: it starts nothing(set to none)");
      expect(within(card).getByTestId("background-model-proposal_brief")).toHaveTextContent("gpt-6-luna(its own)");
    });

    it("shows a read-only user the values, and no way to change them", async () => {
      mockIsAdmin = false;
      mockFetchEffectiveSettings.mockResolvedValue(effectiveSettings({ default: "custom/qwen" }));
      renderPage();
      const card = await screen.findByTestId("background-models");
      const field = within(card).getByLabelText("Background work runs on");
      expect(field).toHaveValue("custom/qwen");
      expect(field).toBeDisabled();
      expect(within(card).getByRole("button", { name: "Save" })).toBeDisabled();
      expect(within(card).getByText("Only an admin can change the settings document.")).toBeInTheDocument();
      expect(within(card).getByTestId("background-model-job_brief")).toHaveTextContent("custom/qwen(the default)");
    });

    it("shows a refused model next to the field, naming its key", async () => {
      mockSetSettingsKeys.mockRejectedValue({
        response: {
          status: 422,
          data: { detail: [{ loc: ["background_models", "default"], msg: "not a model this deployment offers" }] },
        },
      });
      renderPage();
      const card = await screen.findByTestId("background-models");
      await userEvent.type(within(card).getByLabelText("Background work runs on"), "custom/nope");
      await userEvent.click(within(card).getByRole("button", { name: "Save" }));
      expect(await within(card).findByRole("alert")).toHaveTextContent(
        "background_models.default: not a model this deployment offers",
      );
    });

    it("offers and accepts only a model of your own server, as the API does (#287)", async () => {
      mockFetchProviderConfig.mockResolvedValue({ ...PROVIDER_CONFIG, fallback_models: ["grok-4.7", "custom/fallback"] });
      mockFetchEffectiveSettings.mockResolvedValue(
        effectiveSettings({ default: "custom/qwen", scout: "custom/scout", proposal_brief: "gpt-6-luna" }),
      );
      const { container } = renderPage();
      const card = await screen.findByTestId("background-models");
      const offered = [...container.querySelectorAll("#background_model_suggestions option")].map((o) =>
        o.getAttribute("value"),
      );
      expect(offered.sort()).toEqual(["custom/fallback", "custom/qwen", "custom/scout"]);
      expect(card).toHaveTextContent("The model of your own server, custom/<model>");
      expect(card).toHaveTextContent("The scout runs only on a model named for it");

      const field = within(card).getByLabelText("Background work runs on");
      await userEvent.clear(field);
      await userEvent.type(field, "gpt-6-luna");
      expect(within(card).getByText("The default is a model of your own server, custom/<model>.")).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Save" })).toBeDisabled();
      expect(mockSetSettingsKeys).not.toHaveBeenCalled();
    });

    it("says the scout is off until a model is named for it", async () => {
      mockFetchEffectiveSettings.mockResolvedValue(effectiveSettings({ default: "custom/qwen" }));
      renderPage();
      const card = await screen.findByTestId("background-models");
      expect(within(card).getByTestId("background-model-scout")).toHaveTextContent(
        "no model: it starts nothing(until a model is named for it)",
      );
      expect(within(card).getByTestId("background-model-context_audit")).toHaveTextContent("custom/qwen(the default)");
    });

    it("is hidden against a backend without the section", async () => {
      mockFetchEffectiveSettings.mockResolvedValue({ generation: 1, sections: [], unknown_sections: [], unknown_keys: [] });
      renderPage();
      await screen.findByTestId("provider-card-anthropic");
      expect(screen.queryByTestId("background-models")).toBeNull();
    });
  });
});
