import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const mockUseAgents = vi.fn();

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => mockUseAgents(),
  useDeleteAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useResetAgent: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { AgentsPage } from "../AgentsPage";

const AGENTS = [
  {
    type: "developer",
    description: "Writes code",
    model: "claude-opus-5",
    tools: ["Read", "Edit"],
    skills: [],
    system_prompt: "",
    is_builtin: true,
    provider: "anthropic",
    provider_ready: true,
  },
  {
    type: "grok-dev",
    description: "Writes code on grok",
    model: "grok-4.5",
    tools: [],
    skills: [],
    system_prompt: "",
    is_builtin: false,
    provider: "xai",
    provider_ready: false,
  },
  {
    type: "mystery",
    description: "Model nobody serves",
    model: "gpt-4o",
    tools: [],
    skills: [],
    system_prompt: "",
    is_builtin: false,
    provider: null,
    provider_ready: false,
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentsPage />
    </MemoryRouter>,
  );
}

function rowOf(type: string): HTMLElement {
  const cell = screen.getByText(type, { selector: "div" });
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${type}`);
  return row;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAgents.mockReturnValue({ data: AGENTS, isLoading: false });
});

describe("AgentsPage provider column", () => {
  it("has a Provider column between Model and Tools", () => {
    renderPage();
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Type", "Description", "Model", "Provider", "Tools", "Skills", "Actions"]);
  });

  it("names the provider and marks it ready", () => {
    renderPage();
    const row = rowOf("developer");
    expect(within(row).getByText("anthropic")).toBeInTheDocument();
    expect(within(row).getByText("ready")).toBeInTheDocument();
    expect(within(row).queryByText("not ready")).not.toBeInTheDocument();
  });

  it("marks a provider without a credential as not ready", () => {
    renderPage();
    const row = rowOf("grok-dev");
    expect(within(row).getByText("xai")).toBeInTheDocument();
    expect(within(row).getByText("not ready")).toBeInTheDocument();
  });

  it("shows no provider for a model nothing serves", () => {
    renderPage();
    const row = rowOf("mystery");
    expect(within(row).getByText("none")).toBeInTheDocument();
    expect(within(row).getByText("not ready")).toBeInTheDocument();
  });

  it("shows no readiness badge when the backend sends none", () => {
    mockUseAgents.mockReturnValue({
      data: [{ ...AGENTS[0], provider: undefined, provider_ready: undefined }],
      isLoading: false,
    });
    renderPage();
    const row = rowOf("developer");
    expect(within(row).queryByText("ready")).not.toBeInTheDocument();
    expect(within(row).queryByText("not ready")).not.toBeInTheDocument();
  });
});
