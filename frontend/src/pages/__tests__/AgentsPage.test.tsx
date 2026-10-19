import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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
    expect(within(row).getByText("Ready")).toBeInTheDocument();
    expect(within(row).queryByText("Not ready")).not.toBeInTheDocument();
  });

  it("marks a provider without a credential as not ready", () => {
    renderPage();
    const row = rowOf("grok-dev");
    expect(within(row).getByText("xai")).toBeInTheDocument();
    expect(within(row).getByText("Not ready")).toBeInTheDocument();
  });

  it("shows no provider for a model nothing serves", () => {
    renderPage();
    const row = rowOf("mystery");
    expect(within(row).getByText("none")).toBeInTheDocument();
    expect(within(row).getByText("Not ready")).toBeInTheDocument();
  });

  it("shows no readiness badge when the backend sends none", () => {
    mockUseAgents.mockReturnValue({
      data: [{ ...AGENTS[0], provider: undefined, provider_ready: undefined }],
      isLoading: false,
    });
    renderPage();
    const row = rowOf("developer");
    expect(within(row).queryByText("ready")).not.toBeInTheDocument();
    expect(within(row).queryByText("Not ready")).not.toBeInTheDocument();
  });
});

// The eight read-only specialists the backend seeds as built-ins.
const SPECIALISTS = [
  "kintsugi",
  "gardener",
  "interrogator",
  "why",
  "how",
  "blast-radius",
  "comment-sicko",
  "design-auditor",
];

const SPECIALIST_ROWS = SPECIALISTS.map((type) => ({
  type,
  description: `${type} does one job. Read-only.`,
  model: "claude-sonnet-5",
  tools: ["Read", "Glob", "Grep", "Bash"],
  skills: [],
  system_prompt: "prompt",
  is_builtin: true,
  provider: "anthropic",
  provider_ready: true,
}));

describe("AgentsPage specialist agents", () => {
  beforeEach(() => {
    mockUseAgents.mockReturnValue({ data: [...SPECIALIST_ROWS, AGENTS[1]], isLoading: false });
  });

  it("shows each specialist as built-in, with Reset and no Delete", () => {
    renderPage();
    for (const type of SPECIALISTS) {
      const row = rowOf(type);
      expect(within(row).getByText("Built-in")).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: /reset/i })).toBeInTheDocument();
      expect(within(row).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    }
  });

  it("keeps them under the Built-in filter and out of Custom", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Built-in" }));
    for (const type of SPECIALISTS) expect(rowOf(type)).toBeInTheDocument();
    expect(screen.queryByText("grok-dev", { selector: "div" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Custom" }));
    for (const type of SPECIALISTS) {
      expect(screen.queryByText(type, { selector: "div" })).not.toBeInTheDocument();
    }
  });

  it("gives each specialist its own icon on a neutral tile, never a second accent", () => {
    renderPage();
    const marks = SPECIALISTS.map((type) => within(rowOf(type)).getByTestId(`agent-mark-${type}`));
    for (const mark of marks) {
      expect(mark.className).toContain("bg-overlay");
      expect(mark.className).not.toMatch(/text-role-|text-primary|text-blue|text-cyan/);
    }
    const icons = marks.map((mark) => mark.querySelector("svg")?.getAttribute("class") ?? "");
    expect(icons.every((cls) => cls.includes("lucide-"))).toBe(true);
    expect(new Set(icons).size).toBe(SPECIALISTS.length);
  });
});
