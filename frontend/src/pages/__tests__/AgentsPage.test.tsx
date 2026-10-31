import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const mockUseAgents = vi.fn();
const mockUseProgression = vi.fn();

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => mockUseAgents(),
  useDeleteAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useResetAgent: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));

import { AgentsPage } from "../AgentsPage";
import { PACK_VIEW } from "@/components/progression/__tests__/fixtures";

const PROGRESSION = {
  // the tier names come from the pack, in the answer (#176)
  ...PACK_VIEW,
  account: { xp: 2_100_000, total_level: 105, characters: 1 },
  skills: [{ id: "coding", name: "Coding", group: "build" }],
  characters: [
    {
      agent_type: "developer",
      level: 34,
      xp: 2_100_000,
      total_level: 105,
      best_skill: "coding",
      tier: "obsidian",
      skills: [{ stat: "coding", xp: 2_100_000, level: 83, level_xp: 2_000_000, xp_to_next: 50_000 }],
    },
  ],
  achievements: [],
  recent: [],
  computed_at: "2026-10-24T00:00:00Z",
};

const AGENTS = [
  {
    type: "developer",
    display_name: "Developer",
    avatar: "smith",
    color: "amber",
    tagline: "Forges the change.",
    description: "Writes code",
    model: "claude-opus-5",
    tools: ["Read", "Edit"],
    skills: ["superpowers:writing-plans"],
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
  mockUseProgression.mockReturnValue({ data: undefined });
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
    expect(within(row).getByText("Anthropic")).toBeInTheDocument();
    expect(within(row).getByText("Ready")).toBeInTheDocument();
    expect(within(row).queryByText("Not ready")).not.toBeInTheDocument();
  });

  it("marks a provider without a credential as not ready", () => {
    renderPage();
    const row = rowOf("grok-dev");
    expect(within(row).getByText("xAI")).toBeInTheDocument();
    expect(within(row).getByText("Not ready")).toBeInTheDocument();
  });

  it("shows no provider for a model nothing serves", () => {
    renderPage();
    const row = rowOf("mystery");
    expect(within(row).getByText("None")).toBeInTheDocument();
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

// GET /agents reports the model a run uses next to the stored one: a
// deployment default model replaces it, and the pr-reviewer runs the heavy tier.
const ON_DEFAULT = {
  ...AGENTS[0],
  effective_model: "grok-4.7",
  effective_model_source: "default_model",
  effective_provider: "xai",
  effective_provider_ready: true,
};

const PR_REVIEWER = {
  ...AGENTS[0],
  type: "pr-reviewer",
  display_name: "PR Reviewer",
  effective_model: "claude-opus-5",
  effective_model_source: "per_job_heavy_tier",
  effective_provider: "anthropic",
  effective_provider_ready: true,
};

describe("AgentsPage shows what a run uses", () => {
  it("shows the deployment default model and its provider, not the stored ones", () => {
    mockUseAgents.mockReturnValue({ data: [ON_DEFAULT], isLoading: false });
    renderPage();
    const row = rowOf("developer");
    expect(within(row).getByText("grok-4.7"), "the Model column names the model the run uses").toBeInTheDocument();
    expect(within(row).getByText("xAI"), "the Provider column names its provider").toBeInTheDocument();
    expect(within(row).queryByText("Anthropic"), "the stored model's provider is not shown").not.toBeInTheDocument();
    const reason = within(row).getByTestId("run-model-reason");
    expect(reason).toHaveTextContent("Deployment default model");
    expect(reason).toHaveTextContent("stored claude-opus-5");
  });

  it("marks the run's provider not ready even when the stored one is ready", () => {
    mockUseAgents.mockReturnValue({ data: [{ ...ON_DEFAULT, effective_provider_ready: false }], isLoading: false });
    renderPage();
    const row = rowOf("developer");
    expect(within(row).getByText("Not ready")).toBeInTheDocument();
    expect(within(row).queryByText("Ready")).not.toBeInTheDocument();
  });

  it("says the pr-reviewer runs the heavy tier, without a stored note when the models match", () => {
    mockUseAgents.mockReturnValue({ data: [PR_REVIEWER], isLoading: false });
    renderPage();
    const reason = within(rowOf("pr-reviewer")).getByTestId("run-model-reason");
    expect(reason).toHaveTextContent("Heavy tier, named by every job");
    expect(reason).not.toHaveTextContent("stored");
  });

  it("adds no note when an agent runs its own model", () => {
    mockUseAgents.mockReturnValue({
      data: [{ ...AGENTS[0], effective_model: "claude-opus-5", effective_model_source: "agent", effective_provider: "anthropic", effective_provider_ready: true }],
      isLoading: false,
    });
    renderPage();
    expect(within(rowOf("developer")).queryByTestId("run-model-reason")).not.toBeInTheDocument();
  });
});

// The nine read-only specialists the backend seeds as built-ins.
const SPECIALISTS = [
  "kintsugi",
  "gardener",
  "interrogator",
  "why",
  "how",
  "blast-radius",
  "comment-sicko",
  "design-auditor",
  "context-auditor",
  "area-mapper",
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

  it("gives each specialist its own identity sprite, never a lucide tile or a second accent", () => {
    renderPage();
    const sprites = SPECIALISTS.map((type) => {
      const row = rowOf(type);
      const sprite = row.querySelector<SVGElement>("svg[data-avatar]");
      expect(sprite, `${type} carries its identity sprite`).not.toBeNull();
      expect(row.querySelector("svg[class*='lucide-']:not([class*='lucide-rotate-ccw'])"), `${type} has no lucide tile beside the sprite`).toBeNull();
      return `${sprite!.getAttribute("data-avatar")}/${sprite!.getAttribute("data-color") ?? ""}`;
    });
    expect(new Set(sprites).size, "the nine specialists are told apart by their sprites").toBe(SPECIALISTS.length);
  });
});

describe("AgentsPage identity", () => {
  it("shows the row's display name, tagline and avatar tile, and names the skills on hover", () => {
    renderPage();
    const row = rowOf("developer");
    expect(within(row).getByText("Developer")).toBeInTheDocument();
    expect(within(row).getByText("Forges the change.")).toBeInTheDocument();
    expect(row.querySelector('[data-avatar="smith"]'), "the row's avatar tile").not.toBeNull();
    expect(within(row).getByTitle("superpowers:writing-plans")).toHaveTextContent("1");
  });

  it("paints a row without identity fields as the default golem", () => {
    renderPage();
    expect(rowOf("mystery").querySelector('[data-avatar="golem"]'), "the default avatar tile").not.toBeNull();
  });

  it("shows each character's level, and nothing for an agent progression does not list", () => {
    mockUseProgression.mockReturnValue({ data: PROGRESSION });
    renderPage();
    const badges = screen.getAllByTestId("level-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("Level 34");
    expect(badges[0]).toHaveAttribute("title", "Total level 105 · Obsidian tier from Coding 83 · open the skill sheet");
    expect(badges[0]).toHaveAttribute("data-tier", "obsidian");
  });
});

describe("AgentsPage background writers (#166)", () => {
  const WRITER = {
    type: "proposal-brief",
    display_name: "Proposal Brief",
    avatar: "apprentice",
    color: "sky",
    description: "The voice of each owner proposal's brief",
    model: "",
    tools: [],
    skills: [],
    system_prompt: "You explain a piece of proposed engineering work.",
    is_builtin: true,
    runs_in_jobs: false,
    effective_model: "claude-sonnet-5",
    effective_model_source: "builtin_default",
  };

  it("lists a definition no job runs under its own heading, its model from background_models", () => {
    mockUseAgents.mockReturnValue({ data: [...AGENTS, WRITER], isLoading: false });
    renderPage();
    const section = screen.getByTestId("background-writers");
    expect(within(section).getByRole("heading", { name: "Background writers" })).toBeInTheDocument();
    const row = within(section).getByText("Proposal Brief", { selector: "div" }).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByTestId("background-model")).toHaveTextContent("from background_models");
    expect(row, "never the built-in default it would run on in a job").not.toHaveTextContent("claude-sonnet-5");
    const jobs = screen.getAllByRole("table")[0];
    expect(within(jobs).queryByText("Proposal Brief"), "the jobs' agents table leaves it out").toBeNull();
  });

  it("shows no such heading while every definition runs in jobs", () => {
    renderPage();
    expect(screen.queryByTestId("background-writers")).toBeNull();
  });
});
