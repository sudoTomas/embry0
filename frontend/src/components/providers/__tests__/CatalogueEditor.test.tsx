// The Providers page edits the provider catalogue (#173, plan item 6): a form per provider and the overlay's YAML
// under Advanced, each save first reviewed through the server's dry run and refused whole with every problem named.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ProviderCatalogueDocument } from "@/api/providerCatalogue";

const api = vi.hoisted(() => ({
  fetchProviderCatalogue: vi.fn(),
  previewProviderCatalogue: vi.fn(),
  saveProviderCatalogue: vi.fn(),
  CATALOGUE_ROUTE: "/config/providers/catalog",
}));
vi.mock("@/api/providerCatalogue", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { CatalogueEditor } from "../CatalogueEditor";

const model = (prices: { in: number; out: number; from?: string | null }[], extra: object = {}) => ({
  prices,
  free: false,
  chat_only: false,
  request_extras: {},
  ...extra,
});

const SHIPPED: ProviderCatalogueDocument["shipped"] = {
  version: 1,
  providers: {
    anthropic: {
      runtime: "sdk",
      enabled: true,
      model_prefixes: ["claude-"],
      models: { "claude-sonnet-5": model([{ in: 3, out: 15 }]) },
    },
    deepseek: {
      runtime: "chat_completions",
      enabled: false,
      model_prefixes: ["deepseek-"],
      models: {
        "deepseek-flash": model([{ in: 0.3, out: 1.2 }]),
        "deepseek-pro": model([{ in: 1, out: 4 }, { from: "2026-12-01", in: 2, out: 8 }]),
      },
    },
  },
};

function doc(over: Partial<ProviderCatalogueDocument> = {}): ProviderCatalogueDocument {
  return {
    yaml_text: "",
    data: {},
    generation: 4,
    updated_by: null,
    updated_at: null,
    catalogue: SHIPPED,
    shipped: SHIPPED,
    in_force: "whole",
    in_force_generation: 4,
    refused: [],
    notes: [],
    ...over,
  };
}

function renderEditor(isAdmin = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CatalogueEditor isAdmin={isAdmin} />
    </QueryClientProvider>,
  );
}

function rejected(status: number, detail: unknown) {
  return Object.assign(new Error("refused"), { response: { status, data: { detail } } });
}

beforeEach(() => {
  api.fetchProviderCatalogue.mockReset().mockResolvedValue(doc());
  api.previewProviderCatalogue
    .mockReset()
    .mockResolvedValue({ dry_run: true, generation: 4, changed: true, diff: "@@ -0,0 +1 @@\n+providers:\n" });
  api.saveProviderCatalogue.mockReset().mockResolvedValue({ generation: 5, revision_id: 9, changed: true, diff: "" });
});

describe("CatalogueEditor", () => {
  it("turns a provider on and re-prices a model, reviews the change, then saves exactly what it reviewed", async () => {
    const user = userEvent.setup();
    renderEditor();
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" }));
    const price = within(deepseek).getByLabelText("deepseek-flash input price");
    await user.clear(price);
    await user.type(price, "0.5");
    await user.click(screen.getByTestId("catalogue-review-form"));
    const expected =
      "providers:\n  deepseek:\n    enabled: true\n    models:\n      deepseek-flash:\n        prices:\n          - {in: 0.5, out: 1.2}\n";
    await waitFor(() => expect(api.previewProviderCatalogue).toHaveBeenCalledWith(expected, 4));
    const review = await screen.findByTestId("catalogue-review");
    expect(within(review).getByTestId("text-diff")).toHaveTextContent("providers:");
    await user.click(within(review).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.saveProviderCatalogue).toHaveBeenCalledWith(expected, 4, undefined));
  });

  it("names every problem the catalogue finds, and stores nothing", async () => {
    api.previewProviderCatalogue.mockRejectedValue(
      rejected(422, [{ loc: ["providers", "deepseek", "models", "deepseek-new"], type: "unpriced", msg: "a model needs its prices" }]),
    );
    const user = userEvent.setup();
    renderEditor();
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" }));
    await user.click(screen.getByTestId("catalogue-review-form"));
    const problems = await screen.findByTestId("catalogue-problems");
    expect(problems).toHaveTextContent("providers.deepseek.models.deepseek-new");
    expect(problems).toHaveTextContent("a model needs its prices");
    expect(api.saveProviderCatalogue).not.toHaveBeenCalled();
  });

  it("says the catalogue moved since it was opened, and keeps the edits", async () => {
    api.previewProviderCatalogue.mockRejectedValue(rejected(409, "changed"));
    const user = userEvent.setup();
    renderEditor();
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" }));
    await user.click(screen.getByTestId("catalogue-review-form"));
    expect(await screen.findByTestId("catalogue-conflict")).toBeInTheDocument();
    expect(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" })).toHaveAttribute("aria-checked", "true");
  });

  it("adds a model, takes a shipped one out, and puts it back", async () => {
    const user = userEvent.setup();
    renderEditor();
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(
      within(within(deepseek).getByTestId("catalogue-model-deepseek-deepseek-flash")).getByRole("button", {
        name: "Take out",
      }),
    );
    await user.type(within(deepseek).getByLabelText("Add a model"), "deepseek-new");
    await user.click(within(deepseek).getByRole("switch", { name: "New model free" }));
    await user.click(within(deepseek).getByRole("button", { name: "Add" }));
    await user.click(screen.getByTestId("catalogue-review-form"));
    await waitFor(() =>
      expect(api.previewProviderCatalogue).toHaveBeenCalledWith(
        "providers:\n  deepseek:\n    models:\n      deepseek-flash: null\n      deepseek-new:\n        free: true\n",
        4,
      ),
    );
    await user.click(within(screen.getByTestId("catalogue-provider-deepseek")).getByRole("button", { name: "Put back" }));
    await user.click(within(screen.getByTestId("catalogue-provider-deepseek")).getByRole("button", { name: "Remove" }));
    expect(screen.getByTestId("catalogue-review-form")).toBeDisabled();
  });

  it("puts a changed model back to what embry0 ships", async () => {
    api.fetchProviderCatalogue.mockResolvedValue(
      doc({ data: { providers: { deepseek: { models: { "deepseek-flash": { prices: [{ in: 9, out: 9 }] } } } } } }),
    );
    const user = userEvent.setup();
    renderEditor();
    const row = await screen.findByTestId("catalogue-model-deepseek-deepseek-flash");
    expect(row).toHaveTextContent("Changed");
    await user.click(within(row).getByRole("button", { name: "Reset to shipped" }));
    await user.click(screen.getByTestId("catalogue-review-form"));
    await waitFor(() => expect(api.previewProviderCatalogue).toHaveBeenCalledWith("", 4));
  });

  it("leaves dated prices to Advanced and keeps an SDK provider on", async () => {
    renderEditor();
    const row = await screen.findByTestId("catalogue-model-deepseek-deepseek-pro");
    expect(row).toHaveTextContent("Dated prices");
    expect(within(row).queryByLabelText("deepseek-pro input price")).toBeNull();
    const anthropic = screen.getByTestId("catalogue-provider-anthropic");
    expect(within(anthropic).getByRole("switch", { name: "Anthropic enabled" })).toBeDisabled();
    expect(anthropic).toHaveTextContent("Always on");
  });

  it("warns that a save from the form does not keep the stored text's comments", async () => {
    api.fetchProviderCatalogue.mockResolvedValue(
      doc({ yaml_text: "# DeepSeek for the light tier\nproviders:\n  deepseek: {enabled: true}\n", data: { providers: { deepseek: { enabled: true } } } }),
    );
    const user = userEvent.setup();
    renderEditor();
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" }));
    await user.click(screen.getByTestId("catalogue-review-form"));
    expect(await screen.findByTestId("catalogue-comments-dropped")).toBeInTheDocument();
  });

  it("edits the overlay's YAML under Advanced, comments and all", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByTestId("catalogue-provider-deepseek");
    await user.click(screen.getByRole("tab", { name: "Advanced" }));
    const text = "# off-peak\nproviders:\n  deepseek: {enabled: true}\n";
    const box = screen.getByLabelText("The overlay, as YAML");
    await user.clear(box);
    await user.type(box, text.replace(/[{]/g, "{{"));
    await user.click(screen.getByTestId("catalogue-review-yaml"));
    await waitFor(() => expect(api.previewProviderCatalogue).toHaveBeenCalledWith(text, 4));
    expect(screen.queryByTestId("catalogue-comments-dropped")).toBeNull();
    await user.click(within(await screen.findByTestId("catalogue-review")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.saveProviderCatalogue).toHaveBeenCalledWith(text, 4, undefined));
  });

  it("says it needs this release's orchestrator when the read carries no shipped catalogue, and nothing else breaks", async () => {
    const { shipped: _dropped, ...older } = doc();
    api.fetchProviderCatalogue.mockResolvedValue(older);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CatalogueEditor isAdmin />
        <p>the rest of the page</p>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("catalogue-needs-orchestrator")).toHaveTextContent("this release's orchestrator");
    expect(screen.getByText("the rest of the page")).toBeInTheDocument();
    expect(screen.queryByTestId("catalogue-review-form")).toBeNull();
  });

  it("keeps a fault of the card in the card", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    api.fetchProviderCatalogue.mockResolvedValue(doc({ shipped: { version: 1, providers: { broken: null as never } } }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CatalogueEditor isAdmin />
        <p>the rest of the page</p>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("catalogue-failed")).toBeInTheDocument();
    expect(screen.getByText("the rest of the page")).toBeInTheDocument();
  });

  it("keeps each model's marker and action in its first column, reachable on a phone without a sideways scroll", async () => {
    renderEditor();
    const row = await screen.findByTestId("catalogue-model-deepseek-deepseek-flash");
    const first = row.querySelector("td")!;
    expect(within(first).getByText("Shipped")).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Take out" })).toBeInTheDocument();
  });

  it("breaks a long model id after a slash, a hyphen or a dot, never inside a word", async () => {
    const id = "openrouter/openai/gpt-5.1-mini";
    const shipped = {
      ...SHIPPED,
      providers: {
        ...SHIPPED.providers,
        openrouter: {
          runtime: "chat_completions",
          enabled: true,
          model_prefixes: ["openrouter/"],
          models: { [id]: model([{ in: 1, out: 2 }]) },
        },
      },
    } as ProviderCatalogueDocument["shipped"];
    api.fetchProviderCatalogue.mockResolvedValue(doc({ shipped, catalogue: shipped }));
    renderEditor();
    const row = await screen.findByTestId(`catalogue-model-openrouter-${id}`);
    const shown = within(row).getByText(id);
    expect(shown.innerHTML, "the id may break only after its separators").toBe(
      "openrouter/<wbr>openai/<wbr>gpt-<wbr>5.<wbr>1-<wbr>mini",
    );
    expect(shown.className, "no break inside a word").not.toMatch(/break-all/);
  });

  it("names each model's switches beside them, for a row stacked in a narrow card without the table's header", async () => {
    renderEditor();
    const row = await screen.findByTestId("catalogue-model-deepseek-deepseek-flash");
    const free = within(row).getByRole("switch", { name: "deepseek-flash free" });
    const chatOnly = within(row).getByRole("switch", { name: "deepseek-flash chat-only" });
    expect(free.closest("td"), "the free switch's cell names it").toHaveTextContent("Free");
    expect(chatOnly.closest("td"), "the chat-only switch's cell names it").toHaveTextContent("Chat-only");
    // Laid out by the card's width (a container query), not the window's: the header and the row's table layout
    // are only for a card wide enough for the table.
    const table = row.closest("table")!;
    expect(table.querySelector("thead")!.className).toMatch(/(^| )hidden( |$)/);
    expect(table.querySelector("thead")!.className).toMatch(/@lg:table-header-group/);
    expect(row.className).toMatch(/flex-wrap/);
    expect(row.className).toMatch(/@lg:table-row/);
  });

  it("shows a reader the catalogue and nothing to change it with", async () => {
    renderEditor(false);
    const deepseek = await screen.findByTestId("catalogue-provider-deepseek");
    expect(within(deepseek).getByRole("switch", { name: "DeepSeek enabled" })).toBeDisabled();
    expect(within(deepseek).getByLabelText("deepseek-flash input price")).toBeDisabled();
    expect(screen.queryByTestId("catalogue-review-form")).toBeNull();
    expect(within(deepseek).queryByRole("button", { name: "Take out" })).toBeNull();
  });
});
