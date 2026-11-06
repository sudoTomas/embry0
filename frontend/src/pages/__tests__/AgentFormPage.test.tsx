import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Progression } from "@/api/progression";
import { progression as progressionFixture } from "@/components/progression/__tests__/fixtures";
import { useAuthStore } from "@/stores/authStore";
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
const progressionState: { current: { data?: Progression } } = { current: {} };
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => progressionState.current }));
// Hoisted so each hook call returns the same array, as react-query does.
const catalogs = vi.hoisted(() => ({
  tools: [
    { name: "Read", source: "builtin", description: "" },
    { name: "Bash", source: "builtin", description: "" },
    { name: "mcp__playwright__browser_click", source: "mcp:playwright", description: "" },
  ],
  skills: [
    { name: "superpowers:writing-plans", source_id: "superpowers", description: "Plans", available: true, status: "ready" },
  ],
  templates: [{ template_id: "t-1", name: "scout-pass", description: "", created_at: "2026-11-01T00:00:00Z" }],
}));
vi.mock("@/hooks/useTools", () => ({ useTools: () => ({ data: catalogs.tools, isLoading: false }) }));
vi.mock("@/hooks/usePipelines", () => ({ useTemplates: () => ({ data: catalogs.templates, isLoading: false }) }));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: catalogs.skills, isLoading: false }) }));
const toastWarning = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: (...a: unknown[]) => toastWarning(...a) },
}));

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

const GROK_DEV_ROW = {
  ...EXISTING,
  type: "grok-dev",
  description: "Writes code on grok",
  model: "grok-4.5",
  tools: ["Read"],
  skills: [] as string[],
  is_builtin: false,
};

function renderEdit(type = "developer") {
  return render(
    <MemoryRouter initialEntries={[`/agents/${type}`]}>
      <Routes>
        <Route path="/agents/:type" element={<AgentFormPage />} />
        <Route path="/agents" element={<p>agents list</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const renderNew = () => renderEdit("new");

beforeEach(() => {
  // Saving is admin work (require_admin); these tests drive it as the API key.
  useAuthStore.setState({ authMode: "key" });
  vi.clearAllMocks();
  mockUpdateMutate.mockReset();
  mockCreateMutate.mockReset();
  modelsState.current = modelsState.NO_CATALOG;
  progressionState.current = {};
  mockUseAgent.mockReturnValue({ data: EXISTING, isLoading: false });
});

describe("AgentFormPage — execution depth (RAV-1058)", () => {
  it("populates execution mode, auth mode, and mcp servers from the definition", () => {
    renderEdit();
    expect(screen.getByLabelText("Execution mode")).toHaveValue("cli");
    expect(screen.getByLabelText("Auth mode")).toHaveValue("oauth");
    expect(screen.getByLabelText("MCP servers (JSON)")).toHaveValue(
      JSON.stringify(EXISTING.mcp_servers, null, 2),
    );
    expect(screen.getByLabelText("System prompt")).toHaveValue("You write code.");
    expect(screen.getByRole("heading", { level: 1, name: "Edit agent: developer" })).toBeInTheDocument();
  });

  it("saves the full surface, sending null to clear an inherited mode", async () => {
    const user = userEvent.setup();
    renderEdit();

    await user.selectOptions(screen.getByLabelText("Execution mode"), "");
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

    const editor = screen.getByLabelText("MCP servers (JSON)");
    await user.clear(editor);
    await user.type(editor, "not json");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));

    expect(mockUpdateMutate).not.toHaveBeenCalled();
  });
});

describe("AgentFormPage — a definition that names no model (#166)", () => {
  it("saves a brief writer's prompt, still naming no model", async () => {
    const user = userEvent.setup();
    mockUseAgent.mockReturnValue({
      data: {
        ...EXISTING,
        type: "proposal-brief",
        model: "",
        tools: [],
        mcp_servers: {},
        system_prompt: "You explain.",
        runs_in_jobs: false,
      },
      isLoading: false,
    });
    renderEdit("proposal-brief");
    const prompt = screen.getByLabelText("System prompt");
    await user.clear(prompt);
    await user.type(prompt, "Write every brief in plain German.");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));

    expect(mockUpdateMutate, "the edited prompt is saved, with no model forced on the definition").toHaveBeenCalledWith(
      expect.objectContaining({ type: "proposal-brief", model: "", system_prompt: "Write every brief in plain German." }),
      expect.anything(),
    );
  });

  it("still asks a job's agent that names no model for one", async () => {
    const user = userEvent.setup();
    mockUseAgent.mockReturnValue({ data: { ...EXISTING, model: "" }, isLoading: false });
    renderEdit();
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(mockUpdateMutate, "only a definition no job runs may name no model").not.toHaveBeenCalled();
  });

  it("still asks a new agent for its model", async () => {
    const user = userEvent.setup();
    renderNew();
    await user.type(screen.getByLabelText(/type \(slug\)/i), "custom-one");
    await user.selectOptions(screen.getByLabelText("Model"), "__custom__");
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockCreateMutate, "a new agent with no model is not created").not.toHaveBeenCalled();
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
    expect(hint).toHaveTextContent("xAI");
    expect(hint).toHaveTextContent("Not ready");
  });

  it("marks a ready provider", () => {
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, provider: "anthropic", provider_ready: true },
      isLoading: false,
    });
    renderEdit();
    const hint = screen.getByTestId("model-provider-readiness");
    expect(hint).toHaveTextContent("Anthropic");
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
    expect(hint).toHaveTextContent("xAI");
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
    expect(hint).toHaveTextContent("Anthropic");
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
    expect(hint()).toHaveTextContent("Anthropic");
    expect(hint()).not.toHaveTextContent("Not ready");

    await user.selectOptions(screen.getByLabelText("Model"), "__custom__");
    expect(screen.queryByTestId("model-provider-readiness")).not.toBeInTheDocument();

    const custom = screen.getByPlaceholderText("Enter model ID");
    await user.type(custom, "grok-4.5");
    expect(hint()).toHaveTextContent("xAI");
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

describe("AgentFormPage says when jobs run another model", () => {
  const ON_DEFAULT = {
    ...EXISTING,
    effective_model: "grok-4.7",
    effective_model_source: "default_model",
    effective_provider: "xai",
    effective_provider_ready: true,
  };

  it("names the deployment default model that replaces the stored one", () => {
    mockUseAgent.mockReturnValue({ data: ON_DEFAULT, isLoading: false });
    renderEdit();
    expect(screen.getByTestId("model-run-override")).toHaveTextContent(
      "Jobs run this agent on grok-4.7, the deployment default model, not on the model chosen here.",
    );
  });

  it("keeps saying so when another model is picked, since the default replaces any", async () => {
    mockUseAgent.mockReturnValue({ data: ON_DEFAULT, isLoading: false });
    renderEdit();
    await userEvent.selectOptions(screen.getByLabelText("Model"), "claude-sonnet-5");
    expect(screen.getByTestId("model-run-override")).toHaveTextContent("grok-4.7, the deployment default model");
  });

  it("says nothing when the agent runs its own model", () => {
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, effective_model: "claude-opus-5", effective_model_source: "agent" },
      isLoading: false,
    });
    renderEdit();
    expect(screen.queryByTestId("model-run-override")).not.toBeInTheDocument();
  });
});

describe("identity and skills (spec 3.6)", () => {
  it("creates an agent with its identity", async () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    const user = userEvent.setup();
    renderNew();
    await user.type(screen.getByLabelText(/type \(slug\)/i), "release-bot");
    await user.type(screen.getByLabelText(/display name/i), "Release Bot");
    await user.type(screen.getByLabelText(/tagline/i), "Ships it.");
    await user.click(screen.getByRole("radio", { name: "rogue" }));
    await user.click(screen.getByRole("radio", { name: "cyan" }));
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ type: "release-bot", display_name: "Release Bot", tagline: "Ships it.", avatar: "rogue", color: "cyan" }),
      expect.anything(),
    );
    // Typing three fields key by key re-renders the whole form per keystroke;
    // under load that alone outruns the default 5 s (#106).
  }, 15000);
  it("populates identity from the row and shows the preview tile", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, display_name: "Grok Dev", avatar: "bard", color: "sky", tagline: "" }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByLabelText(/display name/i)).toHaveValue("Grok Dev");
    expect(screen.getByTitle("Grok Dev")).toHaveAttribute("data-avatar", "bard");
  });
  it("keeps an empty display name empty through an edit and save", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, display_name: "", avatar: "bard", color: "sky", tagline: "" }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    expect(screen.getByLabelText(/display name/i)).toHaveValue("");
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ display_name: "", tagline: "", avatar: "bard", color: "sky" }),
      expect.anything(),
    );
  });
  it("lists tools from the catalog grouped by source and keeps an unknown current tool", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, tools: ["Read", "mcp__legacy__thing"] }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByText("builtin")).toBeInTheDocument();
    expect(screen.getByText("mcp:playwright")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "mcp__legacy__thing" })).toBeChecked();
  });
  it("adds a catalog skill through the picker and keeps a repository skill", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, skills: ["commit-standard"] }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    // The skill packs' own picker: the section also holds the one for the skills triage may add.
    const packs = document.getElementById(screen.getByLabelText("Skill packs").getAttribute("aria-controls") ?? "");
    await user.click(within(packs!).getByRole("option", { name: /superpowers:writing-plans/ }));
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ skills: ["commit-standard", "superpowers:writing-plans"] }),
      expect.anything(),
    );
  });
  it("toasts every warning the save returns", async () => {
    mockUseAgent.mockReturnValue({ data: GROK_DEV_ROW, isLoading: false });
    mockUpdateMutate.mockImplementation((_u, opts) => opts?.onSuccess?.({ ...GROK_DEV_ROW, warnings: ["superpowers:x is not available"] }));
    const user = userEvent.setup();
    renderEdit("grok-dev");
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(toastWarning).toHaveBeenCalledWith("superpowers:x is not available");
  });
  it("names orphan skills", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, skills: ["old:skill"], orphan_skills: ["old:skill"] }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByText(/no source lists/i)).toHaveTextContent("old:skill");
  });
});

describe("AgentFormPage as a character sheet (progression spec 2026-10-24)", () => {
  const section = (id: string) => screen.getByTestId(`agent-section-${id}`) as HTMLDetailsElement;

  beforeEach(() => {
    mockUseAgent.mockReturnValue({ data: EXISTING, isLoading: false });
    progressionState.current = { data: progressionFixture() };
  });

  it("opens a saved agent on its character, with level, total level and skills, and its configuration closed to a line each", () => {
    renderEdit();
    expect(section("character").open).toBe(true);
    expect(within(section("character")).getByTestId("agent-section-summary")).toHaveTextContent("Level 59 · total level 187 · Divine gear");
    expect(within(section("character")).getByTestId("sheet-levels")).toHaveTextContent("level 59 · total level 187");
    expect(within(section("character")).getAllByTestId("skill-tile").length).toBeGreaterThan(0);
    for (const id of ["identity", "model", "tools", "packs", "mcp", "prompt"]) expect(section(id).open, id).toBe(false);
    const summary = (id: string) => within(section(id)).getByTestId("agent-section-summary");
    expect(summary("model")).toHaveTextContent("claude-opus-5 · cli · oauth");
    expect(summary("tools")).toHaveTextContent("2 tools: Read, Edit");
    expect(summary("packs")).toHaveTextContent("None");
    expect(summary("mcp")).toHaveTextContent("playwright");
    expect(summary("prompt")).toHaveTextContent("15 characters");
  });

  it("opens and closes every part at once", async () => {
    const user = userEvent.setup();
    renderEdit();
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    for (const id of ["character", "identity", "model", "tools", "packs", "mcp", "prompt"]) expect(section(id).open, id).toBe(true);
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    for (const id of ["character", "identity", "model", "tools", "packs", "mcp", "prompt"]) expect(section(id).open, id).toBe(false);
  });

  it("opens the part a save error names", async () => {
    const user = userEvent.setup();
    renderEdit();
    expect(section("mcp").open).toBe(false);
    const editor = screen.getByLabelText("MCP servers (JSON)");
    await user.clear(editor);
    await user.type(editor, "not json");
    expect(within(section("mcp")).getByTestId("agent-section-summary")).toHaveTextContent("Not valid JSON");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(section("mcp").open).toBe(true);
  });

  it("opens every part for a new agent, with no character yet", () => {
    renderNew();
    expect(screen.queryByTestId("agent-section-character")).toBeNull();
    for (const id of ["identity", "model", "tools", "packs", "mcp", "prompt"]) expect(section(id).open, id).toBe(true);
  });
});

describe("AgentFormPage warns about an agent with no tools", () => {
  it("warns that a job runs an agent with no tools with none at all, until a tool is selected", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, tools: [] }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    expect(screen.getByTestId("no-tools-warning")).toHaveTextContent(/no tools at all.*finishes with text only/);
    await user.click(screen.getByRole("checkbox", { name: "Read" }));
    expect(screen.queryByTestId("no-tools-warning"), "a selected tool ends the warning").toBeNull();
  });

  it("warns on a new agent, which starts with no tools", () => {
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    renderNew();
    expect(screen.getByTestId("no-tools-warning")).toBeInTheDocument();
  });

  it("says nothing for an agent with tools, or for a definition no job runs", () => {
    renderEdit();
    expect(screen.queryByTestId("no-tools-warning"), "the developer has tools").toBeNull();
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, type: "proposal-brief", tools: [], runs_in_jobs: false },
      isLoading: false,
    });
    renderEdit("proposal-brief");
    expect(screen.queryByTestId("no-tools-warning"), "a brief writer's voice runs in no job").toBeNull();
  });
});

describe("AgentFormPage: the skills triage may add", () => {
  it("shows what the field does and saves the names picked", async () => {
    mockUseAgent.mockReturnValue({
      data: { ...GROK_DEV_ROW, skills: ["superpowers:writing-plans"], triage_may_add_skills: ["superpowers:writing-plans"] },
      isLoading: false,
    });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    expect(screen.getByTestId("triage-may-add-help")).toHaveTextContent(/only from a source this agent.s skill packs already load/);
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ triage_may_add_skills: ["superpowers:writing-plans"] }),
      expect.anything(),
    );
  });

  it("sends an empty list for an agent that lets triage add nothing", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(expect.objectContaining({ triage_may_add_skills: [] }), expect.anything());
  });

  it("is left out for a definition no job runs", () => {
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, type: "proposal-brief", tools: [], runs_in_jobs: false },
      isLoading: false,
    });
    renderEdit("proposal-brief");
    expect(screen.queryByTestId("triage-may-add-help"), "a brief writer's voice runs in no job").toBeNull();
  });
});

describe("AgentFormPage warns about an agent with browser tools and no shell", () => {
  it("says a job runs it without its browser", () => {
    mockUseAgent.mockReturnValue({
      data: { ...GROK_DEV_ROW, tools: ["Read", "mcp__playwright__browser_click"], mcp_servers: {} },
      isLoading: false,
    });
    renderEdit("grok-dev");
    expect(screen.getByTestId("no-shell-servers-warning")).toHaveTextContent(/without playwright.*without its browser/);
  });

  it("names a command-running server the agent would lose, and says nothing with a shell tool", () => {
    mockUseAgent.mockReturnValue({
      data: { ...GROK_DEV_ROW, tools: ["Read"], mcp_servers: { lint: { type: "stdio", command: "lint-mcp" } } },
      isLoading: false,
    });
    renderEdit("grok-dev");
    expect(screen.getByTestId("no-shell-servers-warning")).toHaveTextContent(/without lint/);
    expect(screen.getByTestId("no-shell-servers-warning")).not.toHaveTextContent(/browser/);
  });

  it("says nothing for an agent with Bash", () => {
    mockUseAgent.mockReturnValue({
      data: { ...GROK_DEV_ROW, tools: ["Read", "Bash", "mcp__playwright__browser_click"] },
      isLoading: false,
    });
    renderEdit("grok-dev");
    expect(screen.queryByTestId("no-shell-servers-warning"), "an agent with a shell keeps its servers").toBeNull();
  });
});

describe("AgentFormPage: picking a skill triage may add", () => {
  it("adds a catalog skill to the list triage may add, not to the skill packs", async () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, skills: ["superpowers:writing-plans"] }, isLoading: false });
    const user = userEvent.setup();
    renderEdit("grok-dev");
    const picker = screen.getByLabelText("Skills triage may add");
    const listbox = document.getElementById(picker.getAttribute("aria-controls") ?? "");
    await user.click(within(listbox!).getByRole("option", { name: /superpowers:writing-plans/ }));
    await user.click(screen.getByRole("button", { name: /save agent/i }));
    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ skills: ["superpowers:writing-plans"], triage_may_add_skills: ["superpowers:writing-plans"] }),
      expect.anything(),
    );
  });
});

describe("AgentFormPage — the guild (#167)", () => {
  it("opens on the row's station and task, and saves what is chosen", async () => {
    const user = userEvent.setup();
    mockUseAgent.mockReturnValue({
      data: { ...GROK_DEV_ROW, type: "scout", station: "lectern", quest_template: "scout-pass" },
      isLoading: false,
    });
    renderEdit("scout");
    expect(screen.getByLabelText("Station")).toHaveValue("lectern");
    expect(screen.getByLabelText("Task from the guild")).toHaveValue("scout-pass");

    const task = screen.getByLabelText("Task from the guild");
    expect([...task.querySelectorAll("option")].map((o) => o.value)).not.toContain("__triage__");
    await user.selectOptions(screen.getByLabelText("Station"), "map_table");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(mockUpdateMutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "scout", station: "map_table", quest_template: "scout-pass" }),
      expect.anything(),
    );

    await user.selectOptions(screen.getByLabelText("Task from the guild"), "__none__");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(mockUpdateMutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ quest_template: null }),
      expect.anything(),
    );
  });

  it("offers a coding agent a task that names no template, and only a coding agent", async () => {
    const user = userEvent.setup();
    mockUseAgent.mockReturnValue({
      data: { ...EXISTING, station: "anvil", quest_template: null, coding_agent: true },
      isLoading: false,
    });
    renderEdit();
    await user.selectOptions(screen.getByLabelText("Task from the guild"), "__triage__");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(mockUpdateMutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "developer", quest_template: "" }),
      expect.anything(),
    );
  });

  it("starts a new agent at a desk with no task from the guild", async () => {
    const user = userEvent.setup();
    mockUseAgent.mockReturnValue({ data: undefined, isLoading: false });
    renderNew();
    expect(screen.getByLabelText("Station")).toHaveValue("desk");
    const task = screen.getByLabelText("Task from the guild");
    expect(task).toHaveValue("__none__");
    expect([...task.querySelectorAll("option")].map((o) => o.value)).not.toContain("__triage__");
    expect([...task.querySelectorAll("option")].map((o) => o.textContent)).toContain("Runs scout-pass");
    await user.type(screen.getByLabelText("Type (slug)"), "scout");
    await user.click(screen.getByRole("button", { name: "Save Agent" }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ type: "scout", station: "desk", quest_template: null }),
      expect.anything(),
    );
  });

  it("keeps a task naming a template the list no longer has", () => {
    mockUseAgent.mockReturnValue({ data: { ...GROK_DEV_ROW, quest_template: "gone-route" }, isLoading: false });
    renderEdit("grok-dev");
    expect(screen.getByLabelText("Task from the guild")).toHaveValue("gone-route");
  });
});
