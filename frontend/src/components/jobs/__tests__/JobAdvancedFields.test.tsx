import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockUseTemplates = vi.fn();
const mockUseSandboxProfiles = vi.fn();
const mockUseAgents = vi.fn();
const mockUseModels = vi.fn();

vi.mock("@/hooks/usePipelines", () => ({ useTemplates: () => mockUseTemplates() }));
vi.mock("@/hooks/useSandboxProfiles", () => ({ useSandboxProfiles: () => mockUseSandboxProfiles() }));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => mockUseAgents() }));
vi.mock("@/hooks/useModels", () => ({ useModels: () => mockUseModels() }));

import {
  EMPTY_ADVANCED,
  JobAdvancedFields,
  advancedToRequest,
} from "../JobAdvancedFields";

beforeEach(() => {
  vi.clearAllMocks();
  mockUseTemplates.mockReturnValue({
    data: [{ template_id: "t1", name: "quick-fix", description: "", created_at: "" }],
  });
  mockUseSandboxProfiles.mockReturnValue({ data: [{ name: "dev-python" }] });
  mockUseAgents.mockReturnValue({
    data: [
      { type: "developer", description: "", model: "m", tools: [], skills: [], system_prompt: "", is_builtin: true },
      {
        type: "job-brief",
        description: "",
        model: "",
        tools: [],
        skills: [],
        system_prompt: "",
        is_builtin: true,
        runs_in_jobs: false,
      },
    ],
  });
  mockUseModels.mockReturnValue({ models: ["claude-opus-5", "claude-sonnet-5"], isLoading: false });
});

describe("advancedToRequest", () => {
  it("omits everything when empty", () => {
    expect(advancedToRequest(EMPTY_ADVANCED)).toEqual({});
  });

  it("maps set values onto the JobCreateRequest field names", () => {
    expect(
      advancedToRequest({
        pipelineTemplate: "quick-fix",
        sandboxProfile: "dev-python",
        maxBudget: "42.5",
        agentModels: { developer: "claude-opus-5", review: "" },
      }),
    ).toEqual({
      pipeline_template: "quick-fix",
      sandbox_profile: "dev-python",
      max_budget_usd: 42.5,
      agent_models: { developer: "claude-opus-5" },
    });
  });

  it("drops a non-positive budget", () => {
    expect(advancedToRequest({ ...EMPTY_ADVANCED, maxBudget: "0" })).toEqual({});
  });
});

describe("JobAdvancedFields", () => {
  it("offers a per-agent model only for the agents a job runs, not a brief writer's voice (#166)", () => {
    render(<JobAdvancedFields value={EMPTY_ADVANCED} onChange={() => {}} />);
    expect(screen.getByLabelText("Model override for developer")).toBeInTheDocument();
    expect(screen.queryByLabelText("Model override for job-brief"), "runs_in_jobs false runs in no job").toBeNull();
  });

  it("renders template, profile, budget, and per-agent model controls", () => {
    render(<JobAdvancedFields value={EMPTY_ADVANCED} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Pipeline Template")).toBeInTheDocument();
    expect(screen.getByLabelText("Sandbox Profile")).toBeInTheDocument();
    expect(screen.getByLabelText("Max Budget (USD)")).toBeInTheDocument();
    expect(screen.getByLabelText("Model override for developer")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "quick-fix" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "dev-python" })).toBeInTheDocument();
  });

  it("reports agent model selection through onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<JobAdvancedFields value={EMPTY_ADVANCED} onChange={onChange} />);
    await user.selectOptions(screen.getByLabelText("Model override for developer"), "claude-opus-5");
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ agentModels: { developer: "claude-opus-5" } }),
    );
  });
});
