import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchModelsCatalog = vi.fn();
const mockRefresh = vi.fn();

vi.mock("@/api/provider", () => ({
  fetchModelsCatalog: () => mockFetchModelsCatalog(),
  fetchProviderConfig: () =>
    Promise.resolve({
      model_heavy: "claude-opus-5",
      model_medium: "claude-opus-5",
      model_light: "claude-opus-5",
      default_model: "",
      disabled_models: [],
      fallback_models: [],
      api_key_set: false,
      oauth_token_set: true,
    }),
  updateProviderConfig: vi.fn(),
  testProviderConnection: vi.fn(),
  testCatalogProvider: vi.fn(),
  fetchProviderUsage: () => Promise.reject(new Error("no spend in this test")),
  refreshOpenRouterCatalog: () => mockRefresh(),
}));

vi.mock("@/api/credentials", () => ({ addProviderKey: vi.fn() }));
vi.mock("@/stores/authStore", () => ({ useIsAdmin: () => true }));

import { ProvidersPage } from "../ProvidersPage";

const LIVE = {
  source: "live",
  models: 40,
  fetched_at: "2026-10-25T08:00:00+00:00",
  skipped: { batch: 74, free: 16, no_tools: 68 },
  last_error: null,
  last_error_at: null,
  refresh_hours: 24,
};

function catalog(status: Record<string, unknown>) {
  return {
    heavy: "claude-opus-5",
    medium: "claude-opus-5",
    light: "claude-opus-5",
    providers: [
      {
        name: "anthropic",
        base_url: "https://api.anthropic.com",
        available: true,
        credential_source: "oauth",
        default: true,
        model_prefixes: ["claude-"],
        models: [{ model: "claude-opus-5", enabled: true, pricing_usd_per_mtok: { input: 5, output: 25 } }],
      },
      {
        name: "openrouter",
        base_url: "https://openrouter.ai/api/v1",
        available: true,
        credential_source: "registry",
        default: false,
        model_prefixes: ["openrouter/"],
        catalog: status,
        models: Array.from({ length: 40 }, (_, i) => ({
          model: i === 39 ? "openrouter/anthropic/claude-opus-5.5" : `openrouter/qwen/model-${String(i).padStart(2, "0")}`,
          name: i === 39 ? "Anthropic: Claude Opus 5.5" : `Qwen: Model ${i}`,
          vendor: i === 39 ? "anthropic" : "qwen",
          enabled: true,
          pricing_usd_per_mtok: { input: 1, output: 2 },
        })),
      },
    ],
  };
}

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
  mockFetchModelsCatalog.mockResolvedValue(catalog(LIVE));
});

describe("ProvidersPage with OpenRouter's live model list", () => {
  it("says where the list comes from, when it was read and what it left out", async () => {
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    const status = within(card).getByTestId("provider-catalog");
    expect(status).toHaveTextContent("OpenRouter's live list, 40 with tool calling, read");
    expect(status).toHaveTextContent("every 24 h");
    expect(status).toHaveTextContent("Left out: 74 batch, 16 free (rate-limited), 68 no tool calling.");
    expect(within(card).queryByTestId("provider-catalog-error")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("provider-card-anthropic")).queryByTestId("provider-catalog")).toBeNull();
  });

  it("reads the list now, and every model selector reads the result", async () => {
    mockRefresh.mockResolvedValue({ ...LIVE, models: 41 });
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    const calls = mockFetchModelsCatalog.mock.calls.length;
    await userEvent.click(within(card).getByRole("button", { name: "Read the list now" }));
    expect(mockRefresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mockFetchModelsCatalog.mock.calls.length).toBeGreaterThan(calls));
  });

  it("shows a failed read beside the list in force", async () => {
    mockFetchModelsCatalog.mockResolvedValue(
      catalog({
        ...LIVE,
        last_error: "OpenRouter answered 503 for its model list",
        last_error_at: "2026-10-25T09:00:00+00:00",
      }),
    );
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    const error = within(card).getByTestId("provider-catalog-error");
    expect(error).toHaveTextContent("The last read failed");
    expect(error).toHaveTextContent("OpenRouter answered 503 for its model list. The list in force stays.");
    expect(within(card).getByTestId("provider-catalog")).toHaveTextContent("40 with tool calling");
  });

  it("says the built-in entry serves until the list is read", async () => {
    mockFetchModelsCatalog.mockResolvedValue(catalog({ source: "static" }));
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    expect(within(card).getByTestId("provider-catalog")).toHaveTextContent(
      "the built-in entry, until OpenRouter's list is read.",
    );
  });

  it("shows a long model table in part, filters it, and shows it all on request", async () => {
    renderPage();
    const card = await screen.findByTestId("provider-card-openrouter");
    const rows = () => within(card).getAllByRole("checkbox", { name: /^Enable / });
    expect(rows()).toHaveLength(25);

    await userEvent.type(within(card).getByRole("searchbox", { name: "Filter OpenRouter models" }), "claude");
    expect(rows().map((r) => r.getAttribute("aria-label"))).toEqual(["Enable openrouter/anthropic/claude-opus-5.5"]);

    await userEvent.clear(within(card).getByRole("searchbox", { name: "Filter OpenRouter models" }));
    await userEvent.click(within(card).getByRole("button", { name: "Show all 40 models" }));
    expect(rows()).toHaveLength(40);
  });
});
