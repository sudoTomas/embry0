import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = {
  probe: vi.fn(),
  register: vi.fn(),
  discover: vi.fn(),
  runs: vi.fn(),
  repos: vi.fn(),
  needs: vi.fn(),
  action: vi.fn(),
};
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return {
    ...actual,
    probeOrganization: (...a: unknown[]) => api.probe(...a),
    registerOrganization: (...a: unknown[]) => api.register(...a),
    discoverOrganization: (...a: unknown[]) => api.discover(...a),
    fetchOrganizationRuns: (...a: unknown[]) => api.runs(...a),
    fetchOrganizationRepos: (...a: unknown[]) => api.repos(...a),
    fetchOrganizationNeeds: (...a: unknown[]) => api.needs(...a),
    runOrganizationAction: (...a: unknown[]) => api.action(...a),
  };
});
vi.mock("@/api/credentials", () => ({
  fetchCredentials: () =>
    Promise.resolve([
      { id: "cred-1", kind: "forge_token", provider: "github", status: "ok", label: "ci bot", verified_login: "acme-bot" },
      { id: "cred-2", kind: "llm_api_key", provider: "anthropic", status: "ok", label: "model key", verified_login: null },
    ]),
}));

import { useAuthStore } from "@/stores/authStore";
import { OrgWizardPage } from "../OrgWizardPage";
import { ACCESS, need, needs, org, repo, run } from "@/components/orgs/__tests__/fixtures";

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/orgs/-/new"]}>
        <Routes>
          <Route path="/orgs/-/new" element={<OrgWizardPage />} />
          <Route path="/orgs/:owner" element={<div data-testid="org-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const current = () =>
  screen.getAllByTestId("wizard-step").find((s) => s.getAttribute("aria-current") === "step")?.textContent;

async function connect(owner = "Acme") {
  fireEvent.change(screen.getByTestId("wizard-owner"), { target: { value: owner } });
  fireEvent.click(screen.getByTestId("wizard-probe"));
}

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  useAuthStore.setState({ authMode: "key" });
  api.probe.mockResolvedValue(ACCESS);
  api.register.mockResolvedValue(org());
  api.discover.mockResolvedValue(run({ status: "running", repos_seen: 0, repos_total: 2 }));
  api.repos.mockResolvedValue([repo("widgets", { registered: true }), repo("gadgets")]);
  api.needs.mockResolvedValue(
    needs([need("n1", "not_registered", ["acme/gadgets"]), need("n2", "no_webhook", ["acme/widgets", "acme/gadgets"])]),
  );
  api.action.mockImplementation(async (_o: string, body: { action: string; repos: string[]; dry_run: boolean }) => ({
    owner: "acme",
    action: body.action,
    dry_run: body.dry_run,
    results: body.repos.map((r) => ({ repo: r, outcome: body.dry_run ? "skipped" : "done", detail: "" })),
  }));
});

afterEach(() => {
  useAuthStore.setState({ authMode: null });
});

describe("OrgWizardPage", () => {
  it("walks connect, discover, report and act", async () => {
    api.runs
      .mockResolvedValueOnce([run({ status: "running", repos_seen: 1, repos_total: 2 })])
      .mockResolvedValue([run()]);
    renderWizard();
    expect(current()).toContain("Connect");
    expect(screen.getByTestId("wizard-next-1")).toBeDisabled();

    // The credential list offers forge tokens for the chosen forge only.
    const options = await within(screen.getByTestId("wizard-credential")).findAllByRole("option");
    await waitFor(() => expect(within(screen.getByTestId("wizard-credential")).getAllByRole("option")).toHaveLength(2));
    expect(options[0]).toHaveTextContent("Environment token for this owner");

    await connect();
    const result = await screen.findByTestId("wizard-probe-result");
    expect(api.probe).toHaveBeenCalledWith({ owner: "acme", forge: "github", credential_id: undefined });
    expect(within(result).getByTestId("wizard-probe-count")).toHaveTextContent("12 repositories");
    expect(within(result).getByTestId("access-hooks")).toHaveAttribute("data-state", "yes");
    expect(result).toHaveTextContent("can manage webhooks");

    fireEvent.click(screen.getByTestId("wizard-next-1"));
    expect(current()).toContain("Discover");
    expect(screen.getByTestId("wizard-next-2")).toBeDisabled();

    fireEvent.click(screen.getByTestId("wizard-register"));
    // The run discover returned shows at once; the first poll moves it on.
    expect(await screen.findByTestId("discovery-progress")).toHaveTextContent("0 of 2 repositories");
    await waitFor(() => expect(screen.getByTestId("discovery-progress")).toHaveTextContent("1 of 2 repositories"));
    expect(api.register).toHaveBeenCalledWith({ owner: "acme", forge: "github", credential_id: undefined });
    expect(api.discover).toHaveBeenCalledWith("acme");

    // The next poll finds the run done.
    expect(await screen.findByTestId("discovery-summary", {}, { timeout: 4000 })).toHaveTextContent("registered");
    fireEvent.click(screen.getByTestId("wizard-next-2"));
    expect(current()).toContain("Report");

    expect(await screen.findAllByTestId("org-repo-row")).toHaveLength(2);
    const groups = await screen.findAllByTestId("org-need-group");
    expect(groups.map((g) => g.getAttribute("data-kind"))).toEqual(["not_registered", "no_webhook"]);
    expect(within(groups[1]).getByTestId("org-need-group-counts")).toHaveTextContent("1 need, 2 repositories");

    fireEvent.click(screen.getByTestId("wizard-next-3"));
    expect(current()).toContain("Act");
    fireEvent.click(screen.getByTestId("org-act-select-all"));
    fireEvent.click(screen.getByTestId("org-act-preview-button"));
    await screen.findByTestId("org-act-preview");
    fireEvent.click(screen.getByTestId("org-act-apply-button"));
    await screen.findByTestId("org-act-results");
    expect(api.action).toHaveBeenCalledWith("acme", {
      action: "register",
      repos: ["acme/gadgets"],
      dry_run: false,
    });
    expect(screen.getByTestId("wizard-finish")).toHaveAttribute("href", "/orgs/acme");
  }, 10_000);

  // A server without POST /orgs/probe answers it with FastAPI's 405 (GET
  // /orgs/{owner} owns the path), or 404 behind a proxy that knows no route.
  it.each([
    [405, "Method Not Allowed"],
    [404, "Not Found"],
  ])("goes on without a preview when the server has no probe endpoint (%i)", async (status, detail) => {
    api.probe.mockRejectedValue({ response: { status, data: { detail } } });
    renderWizard();
    await connect();
    expect(await screen.findByTestId("wizard-probe-missing")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("wizard-next-1")).toBeEnabled();
  });

  it("shows the probe's own 404 as an error and stays on connect", async () => {
    api.probe.mockRejectedValue({ response: { status: 404, data: { detail: "credential not found" } } });
    renderWizard();
    await connect();
    expect(await screen.findByTestId("wizard-probe-error")).toHaveTextContent("credential not found");
    expect(screen.queryByTestId("wizard-probe-missing")).toBeNull();
    expect(screen.getByTestId("wizard-next-1")).toBeDisabled();
  });

  it("stops at connect when the credential cannot list the owner", async () => {
    api.probe.mockResolvedValue({ ...ACCESS, read: false, hooks: null, detail: "acme is not visible" });
    renderWizard();
    await connect();
    expect(await screen.findByTestId("wizard-probe-result")).toHaveTextContent("acme is not visible");
    expect(screen.getByTestId("wizard-next-1")).toBeDisabled();
  });

  it("forgets the probe when the owner changes", async () => {
    renderWizard();
    await connect();
    await screen.findByTestId("wizard-probe-result");
    fireEvent.change(screen.getByTestId("wizard-owner"), { target: { value: "other" } });
    expect(screen.queryByTestId("wizard-probe-result")).toBeNull();
    expect(screen.getByTestId("wizard-next-1")).toBeDisabled();
  });

  it("refuses an owner with a slash before calling the server", () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("wizard-owner"), { target: { value: "acme/widgets" } });
    expect(screen.getByTestId("wizard-probe")).toBeDisabled();
  });

  it("shows the register refusal in the server's words and stays on discover", async () => {
    api.register.mockRejectedValue({ response: { status: 422, data: { detail: "env:GITHUB_TOKEN cannot list acme" } } });
    renderWizard();
    await connect();
    await screen.findByTestId("wizard-probe-result");
    fireEvent.click(screen.getByTestId("wizard-next-1"));
    fireEvent.click(screen.getByTestId("wizard-register"));
    expect(await screen.findByTestId("wizard-register-error")).toHaveTextContent("cannot list acme");
    expect(api.discover).not.toHaveBeenCalled();
    expect(current()).toContain("Discover");
  });

  // Each button starts a new run after a failed one; the wizard must poll
  // the runs again until the new run is done, then let the operator on.
  it.each([
    ["Retry discovery", "wizard-retry-discovery"],
    ["Register and discover again", "wizard-register"],
  ])("polls a run started by %s until it is done", async (_label, button) => {
    const failed = run({ id: "odr-1", status: "error", error: "RuntimeError: rate limited" });
    const second = run({ id: "odr-2", status: "running", repos_seen: 0, repos_total: 2, summary: {} });
    api.discover.mockResolvedValueOnce(run({ id: "odr-1", status: "running" })).mockResolvedValueOnce(second);
    let pollsAfterRetry = 0;
    api.runs.mockImplementation(async () => {
      if (api.discover.mock.calls.length < 2) return [failed];
      pollsAfterRetry += 1;
      return pollsAfterRetry === 1 ? [{ ...second, repos_seen: 1 }, failed] : [run({ id: "odr-2" }), failed];
    });
    renderWizard();
    await connect();
    await screen.findByTestId("wizard-probe-result");
    fireEvent.click(screen.getByTestId("wizard-next-1"));
    fireEvent.click(screen.getByTestId("wizard-register"));
    expect(await screen.findByTestId("discovery-error")).toHaveTextContent("rate limited");
    expect(screen.getByTestId("wizard-next-2")).toBeDisabled();

    fireEvent.click(screen.getByTestId(button));
    expect(await screen.findByTestId("discovery-progress")).toHaveTextContent("of 2 repositories");
    expect(await screen.findByTestId("discovery-summary", {}, { timeout: 4000 })).toHaveTextContent("registered");
    expect(pollsAfterRetry).toBeGreaterThanOrEqual(2);
    expect(api.discover).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("wizard-next-2")).toBeEnabled();

    // A finished run stops the polling.
    const settled = api.runs.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    expect(api.runs.mock.calls.length).toBe(settled);
  }, 15_000);

  it("shows the needs EmptyState when the server has no needs endpoint", async () => {
    api.needs.mockResolvedValue(null);
    api.runs.mockResolvedValue([run()]);
    api.discover.mockResolvedValue(run());
    renderWizard();
    await connect();
    await screen.findByTestId("wizard-probe-result");
    fireEvent.click(screen.getByTestId("wizard-next-1"));
    fireEvent.click(screen.getByTestId("wizard-register"));
    await screen.findByTestId("discovery-summary");
    fireEvent.click(screen.getByTestId("wizard-next-2"));
    expect(await screen.findByText("This server has no needs endpoint yet")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
