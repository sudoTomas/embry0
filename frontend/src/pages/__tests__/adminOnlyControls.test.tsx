// The API reserves writing agents (POST, PUT, DELETE /agents and
// POST /agents/{type}/reset) and PUT /config/integrations for admins. An
// operator session must not be offered those controls as live buttons that
// can only fail with a 403; an admin session keeps them.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";

const AGENTS = [
  { type: "developer", description: "Writes code", model: "claude-opus-5", tools: [], skills: [], system_prompt: "",
    is_builtin: true, provider: "anthropic", provider_ready: true },
  { type: "helper", description: "Custom", model: "claude-opus-5", tools: [], skills: [], system_prompt: "",
    is_builtin: false, provider: "anthropic", provider_ready: true },
];

// Referentially stable results: a fresh object per call re-fires the agent
// form's populate effect forever.
const stable = vi.hoisted(() => ({
  agents: { data: undefined as unknown, isLoading: false },
  agent: { data: undefined as unknown, isLoading: false },
  models: { models: ["claude-opus-5"], providers: [], isLoading: false },
}));
stable.agents.data = AGENTS;
stable.agent.data = AGENTS[0];

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => stable.agents,
  useAgent: () => stable.agent,
  useDeleteAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useResetAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateAgent: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAgent: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => stable.models,
}));
vi.mock("@/api/integrations", () => ({
  fetchIntegrationConfig: vi.fn(async () => ({ trigger_labels: ["embry0"], linear_repo_map: "{}", linear_api_key_set: false })),
  updateIntegrationConfig: vi.fn(),
}));

import { AgentsPage } from "../AgentsPage";
import { AgentFormPage } from "../AgentFormPage";
import IntegrationsTab from "../settings/IntegrationsTab";

function wrap(ui: React.ReactNode, path = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function asOperator() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "o@x", name: "O", tier: "operator" } });
}
function asAdmin() {
  useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "a@x", name: "A", tier: "admin" } });
}

beforeEach(asOperator);

describe("admin-only controls for an operator", () => {
  it("Agents: Reset, Delete and New agent are disabled", () => {
    wrap(<AgentsPage />);
    expect(screen.getByRole("button", { name: /Reset/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /New agent/ })).toBeDisabled();
  });

  it("the agent form's Save is disabled", () => {
    wrap(
      <Routes>
        <Route path="/agents/:type" element={<AgentFormPage />} />
      </Routes>,
      "/agents/developer",
    );
    expect(screen.getByRole("button", { name: /Save Agent/ })).toBeDisabled();
  });

  it("Settings > Integrations Save stays disabled after an edit", async () => {
    wrap(<IntegrationsTab />);
    const repoMap = await screen.findByLabelText(/Project \/ Team/i);
    fireEvent.change(repoMap, { target: { value: '{"A": "o/a"}' } });
    expect(screen.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  });
});

describe("admin-only controls for an admin", () => {
  it("Agents: Reset and Delete stay live", () => {
    asAdmin();
    wrap(<AgentsPage />);
    expect(screen.getByRole("button", { name: /Reset/ })).toBeEnabled();
    expect(within(screen.getByRole("button", { name: /Delete/ })).queryByText("Delete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeEnabled();
  });
});
