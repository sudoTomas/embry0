import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";

const mockUseAgent = vi.fn();
const mockUpdateMutate = vi.fn();
const mockCreateMutate = vi.fn();

vi.mock("@/hooks/useAgents", () => ({
  useAgent: (type: string | null) => mockUseAgent(type),
  useCreateAgent: () => ({ mutate: mockCreateMutate, isPending: false }),
  useUpdateAgent: () => ({ mutate: mockUpdateMutate, isPending: false }),
}));
// Referentially stable per test: a fresh array per call re-fires AgentFormPage's
// populate effect forever (the same loop the useModels memo guards against).
// NO_CATALOG is a backend without the provider catalog; CATALOG has one where
// anthropic is ready and xai has no credential (its grok-4.7 tier still listed,
// its catalogue grok-4.5 left out by useModels, grok-3 operator-disabled).
// NO_CREDENTIALS is the same catalogue seen by an API process that holds no
// credential at all: useModels lists the tier trio only.
const modelsState = vi.hoisted(() => {
  const NO_CATALOG = { models: ["claude-opus-5", "claude-sonnet-5"], providers: [] as unknown[], isLoading: false };
  const price = { input: 1, output: 1 };
  const anthropic = {
    name: "anthropic",
    base_url: "https://api.anthropic.com",
    available: true,
    model_prefixes: ["claude-"],
    models: [
      { model: "claude-opus-5", pricing_usd_per_mtok: price },
      { model: "claude-haiku-4-5", pricing_usd_per_mtok: price },
    ],
  };
  const xai = {
    name: "xai",
    base_url: "https://api.x.ai",
    available: false,
    model_prefixes: ["grok-"],
    models: [
      { model: "grok-4.5", pricing_usd_per_mtok: price },
      { model: "grok-4.7", pricing_usd_per_mtok: price },
      { model: "grok-3", enabled: false, pricing_usd_per_mtok: price },
    ],
  };
  const CATALOG = {
    models: ["grok-4.7", "claude-opus-5", "claude-haiku-4-5"],
    providers: [anthropic, xai],
    isLoading: false,
  };
  const NO_CREDENTIALS = {
    models: ["grok-4.7", "claude-opus-5", "claude-haiku-4-5"],
    providers: [{ ...anthropic, available: false }, xai],
    isLoading: false,
  };
  return { NO_CATALOG, CATALOG, NO_CREDENTIALS, current: NO_CATALOG };
});
vi.mock("@/hooks/useModels", () => ({ useModels: () => modelsState.current }));

import { AgentFormPage } from "../AgentFormPage";

const EXISTING = {
  type: "developer",
  description: "Writes code",
  model: "claude-opus-5",
  tools: ["Read", "Edit"],
  skills: [],
  system_prompt: "You write code.",
  execution_mode: "cli",
  auth_mode: "oauth",
  mcp_servers: { playwright: { type: "stdio", command: "npx" } },
  is_builtin: true,
};

function renderEdit(type = "developer") {
  return render(
    <MemoryRouter initialEntries={[`/agents/${type}`]}>
      <Routes>
        <Route path="/agents/:type" element={<AgentFormPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  modelsState.current = modelsState.NO_CATALOG;
  mockUseAgent.mockReturnValue({ data: EXISTING, isLoading: false });
});

describe("AgentFormPage — execution depth (RAV-1058)", () => {
  it("populates execution mode, auth mode, and mcp servers from the definition", () => {
    renderEdit();
    expect(screen.getByLabelText("Execution Mode")).toHaveValue("cli");
    expect(screen.getByLabelText("Auth Mode")).toHaveValue("oauth");
    expect(screen.getByLabelText("MCP Servers (JSON)")).toHaveValue(
      JSON.stringify(EXISTING.mcp_servers, null, 2),
    );
  });

  it("saves the full surface, sending null to clear an inherited mode", async () => {
    const user = userEvent.setup();
    renderEdit();

    await user.selectOptions(screen.getByLabelText("Execution Mode"), "");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));

    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "developer",
        execution_mode: null,
        auth_mode: "oauth",
        mcp_servers: EXISTING.mcp_servers,
      }),
      expect.anything(),
    );
  });

  it("blocks saving invalid MCP servers JSON", async () => {
    const user = userEvent.setup();
    renderEdit();

    const editor = screen.getByLabelText("MCP Servers (JSON)");
    await user.clear(editor);
    await user.type(editor, "not json");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));

    expect(mockUpdateMutate).not.toHaveBeenCalled();
  });
});

describe("AgentFormPage provider readiness without the catalog", () => {
  it("shows the saved model's provider and that it is not ready", () => {
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, model: "grok-4.5", provider: "xai", provider_ready: false },
      isLoading: false,
    });
    renderEdit();
    const hint = screen.getByTestId("model-provider-readiness");
    expect(hint).toHaveTextContent("xai");
    expect(hint).toHaveTextContent("Not ready");
  });

  it("marks a ready provider", () => {
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, provider: "anthropic", provider_ready: true },
      isLoading: false,
    });
    renderEdit();
    const hint = screen.getByTestId("model-provider-readiness");
    expect(hint).toHaveTextContent("anthropic");
    expect(hint).toHaveTextContent("Ready");
    expect(hint).not.toHaveTextContent("Not ready");
  });

  it("shows nothing when the backend sends no readiness", () => {
    renderEdit();
    expect(screen.queryByTestId("model-provider-readiness")).not.toBeInTheDocument();
  });
});

describe("AgentFormPage provider readiness from the catalog", () => {
  beforeEach(() => {
    modelsState.current = modelsState.CATALOG;
  });

  function optionLabels(): string[] {
    return within(screen.getByLabelText("Model"))
      .getAllByRole("option")
      .map((o) => o.textContent ?? "");
  }

  function option(value: string): HTMLOptionElement {
    const found = within(screen.getByLabelText("Model"))
      .getAllByRole<HTMLOptionElement>("option")
      .find((o) => o.value === value);
    if (!found) throw new Error(`no option ${value}`);
    return found;
  }

  it("offers every model, marking those whose provider has no credential and closing them to a new pick", () => {
    renderEdit();
    expect(optionLabels()).toEqual([
      "grok-4.7 (no credential)",
      "claude-opus-5",
      "claude-haiku-4-5",
      "grok-4.5 (no credential)",
      "Custom...",
    ]);
    expect(option("grok-4.7")).toBeDisabled();
    expect(option("grok-4.5")).toBeDisabled();
    expect(option("claude-haiku-4-5")).toBeEnabled();
  });

  it("opens a saved model whose provider is not ready on that model, with a not-ready warning", () => {
    mockUseAgent.mockReturnValue({ data: { ...EXISTING, model: "grok-4.5" }, isLoading: false });
    renderEdit();
    expect(screen.getByLabelText("Model")).toHaveValue("grok-4.5");
    expect(screen.queryByPlaceholderText("Enter model ID")).not.toBeInTheDocument();
    expect(option("grok-4.5")).toBeEnabled();
    expect(option("grok-4.7")).toBeDisabled();
    const hint = screen.getByTestId("model-provider-readiness");
    expect(hint).toHaveTextContent("xai");
    expect(hint).toHaveTextContent("Not ready");
  });

  it("keeps every model and the saved one when this process sees no credential at all", () => {
    modelsState.current = modelsState.NO_CREDENTIALS;
    renderEdit();
    expect(optionLabels()).toEqual([
      "grok-4.7 (no credential)",
      "claude-opus-5 (no credential)",
      "claude-haiku-4-5 (no credential)",
      "grok-4.5 (no credential)",
      "Custom...",
    ]);
    expect(screen.getByLabelText("Model")).toHaveValue("claude-opus-5");
    expect(option("claude-opus-5")).toBeEnabled();
    expect(option("claude-haiku-4-5")).toBeDisabled();
    const hint = screen.getByTestId("model-provider-readiness");
    expect(hint).toHaveTextContent("anthropic");
    expect(hint).toHaveTextContent("Not ready");
  });

  it("starts a new agent on Custom when no model is ready, every model still listed", () => {
    modelsState.current = modelsState.NO_CREDENTIALS;
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    renderEdit("new");
    expect(screen.getByLabelText("Model")).toHaveValue("__custom__");
    for (const m of ["grok-4.7", "claude-opus-5", "claude-haiku-4-5", "grok-4.5"]) {
      expect(option(m)).toBeDisabled();
    }
  });

  it("names the provider of the chosen model as the choice changes", async () => {
    const user = userEvent.setup();
    renderEdit();
    const hint = () => screen.getByTestId("model-provider-readiness");
    expect(hint()).toHaveTextContent("anthropic");
    expect(hint()).not.toHaveTextContent("Not ready");

    await user.selectOptions(screen.getByLabelText("Model"), "__custom__");
    expect(screen.queryByTestId("model-provider-readiness")).not.toBeInTheDocument();

    const custom = screen.getByPlaceholderText("Enter model ID");
    await user.type(custom, "grok-4.5");
    expect(hint()).toHaveTextContent("xai");
    expect(hint()).toHaveTextContent("Not ready");

    await user.clear(custom);
    await user.type(custom, "gpt-4o");
    expect(hint()).toHaveTextContent("No configured provider serves this model");
    expect(hint()).toHaveTextContent("Not ready");
  });

  it("starts a new agent on the first ready model when the default is not offered", () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    renderEdit("new");
    expect(screen.getByLabelText("Model")).toHaveValue("claude-opus-5");
  });

  it("keeps the default model for a new agent when it is offered", () => {
    modelsState.current = modelsState.NO_CATALOG;
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    renderEdit("new");
    expect(screen.getByLabelText("Model")).toHaveValue("claude-sonnet-5");
  });
});
