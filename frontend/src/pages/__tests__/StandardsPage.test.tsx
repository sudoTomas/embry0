import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = { standards: vi.fn(), versions: vi.fn(), repos: vi.fn(), orgs: vi.fn(), org: vi.fn(), jobs: vi.fn(), job: vi.fn() };
vi.mock("@/api/standards", async () => {
  const actual = await vi.importActual<typeof import("@/api/standards")>("@/api/standards");
  return {
    ...actual,
    fetchOwnerStandards: (...a: unknown[]) => api.standards(...a),
    fetchStandardVersions: (...a: unknown[]) => api.versions(...a),
  };
});
vi.mock("@/api/repos", () => ({ fetchRepos: (...a: unknown[]) => api.repos(...a) }));
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return {
    ...actual,
    fetchOrganizations: (...a: unknown[]) => api.orgs(...a),
    fetchOrganization: (...a: unknown[]) => api.org(...a),
  };
});
vi.mock("@/api/jobs", () => ({
  fetchJobs: (...a: unknown[]) => api.jobs(...a),
  fetchJob: (...a: unknown[]) => api.job(...a),
}));

import { useAuthStore } from "@/stores/authStore";
import { org } from "@/components/orgs/__tests__/fixtures";
import { ownerStandards, standard, state } from "@/components/standards/__tests__/fixtures";
import { StandardsPage } from "../StandardsPage";

function renderPage(path = "/orgs/Acme/standards") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/orgs/:owner/standards" element={<StandardsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  useAuthStore.setState({ authMode: "key", user: null, keyTier: undefined });
  api.orgs.mockResolvedValue([org({ owner: "other" }), org({ display_name: "Acme Inc" })]);
  api.org.mockRejectedValue(new Error("the view reads the organizations list, not one organization"));
  api.standards.mockResolvedValue(
    ownerStandards({
      repos: [
        state("acme/low", { mode: "advise", score: 20, last_audit_at: "2026-01-01T00:00:00Z", last_audit_job_id: "job-1" }),
      ],
    }),
  );
  api.versions.mockResolvedValue([standard(), standard({ id: "std-0", version: 1, status: "superseded" })]);
  api.repos.mockResolvedValue([{ repo: "acme/low" }, { repo: "acme/quiet" }, { repo: "other/x" }]);
  api.jobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
});

describe("StandardsPage", () => {
  it("shows the active standard, its parts and versions, for the owner in lowercase", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Design standard" })).toBeInTheDocument();
    expect(api.standards).toHaveBeenCalledWith("acme");
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByTestId("standard-prose")).toHaveTextContent("Dense and dark.");
    expect(screen.getByTestId("standard-references")).toHaveTextContent(/^References/);
    expect(screen.getByTestId("standard-references")).toHaveTextContent("example.com/brand");
    expect(screen.getAllByTestId("standard-colour")).toHaveLength(2);
    expect(screen.getAllByTestId("standard-type-step")).toHaveLength(3);
    expect(screen.getAllByTestId("standard-rule")).toHaveLength(2);
    expect(await screen.findAllByTestId("standard-version-row")).toHaveLength(2);
    expect(api.versions).toHaveBeenCalledWith("acme", "design");
    expect(await screen.findByRole("link", { name: "← Acme Inc" })).toHaveAttribute("href", "/orgs/acme");
  });

  it("lists the owner's registered repositories, lowest score first, and no other owner's", async () => {
    renderPage();
    const held = await screen.findAllByTestId("standard-repo-row");
    expect(held.map((r) => r.getAttribute("data-repo"))).toEqual(["acme/low"]);
    expect(within(held[0]).getByTestId("standard-score")).toHaveTextContent("20");
    fireEvent.click(screen.getByTestId("standard-repos-off-toggle"));
    const rows = screen.getAllByTestId("standard-repo-row");
    expect(rows.map((r) => r.getAttribute("data-repo"))).toEqual(["acme/low", "acme/quiet"]);
    expect(within(rows[1]).getByTestId("standard-mode-select")).toHaveValue("off");
  });

  it("folds the mode-off repositories, so the standard's parts follow the held ones", async () => {
    api.repos.mockResolvedValue([{ repo: "acme/low" }, ...Array.from({ length: 140 }, (_, i) => ({ repo: `acme/off-${i}` }))]);
    renderPage();
    await screen.findAllByTestId("standard-repo-row");
    expect(screen.getAllByTestId("standard-repo-row")).toHaveLength(1);
    expect(screen.getByTestId("standard-repos-off-toggle")).toHaveTextContent("Show 140 in mode off");
    // The colour roles render without unfolding anything.
    const colours = screen.getByTestId("standard-colours-section");
    expect(within(colours).getAllByTestId("standard-colour")).toHaveLength(2);
    expect(screen.getByTestId("standard-repos-section").compareDocumentPosition(colours)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("names the API path to write a version, and offers no editor", async () => {
    renderPage();
    expect(await screen.findByTestId("standard-put-path")).toHaveTextContent("PUT /api/v1/standards/acme/design");
    expect(screen.queryByRole("textbox", { name: /json/i })).toBeNull();
  });

  it("says when the owner has no standard yet, and still lists the repositories", async () => {
    api.standards.mockResolvedValue(ownerStandards({ standards: [] }));
    api.versions.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByTestId("standard-none")).toHaveTextContent("No design standard for acme yet");
    expect(screen.getByTestId("standard-none")).toHaveTextContent("PUT /api/v1/standards/acme/design");
    expect(await screen.findByTestId("standard-repos-none-held")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("standard-repos-off-toggle"));
    expect(screen.getAllByTestId("standard-repo-row")).toHaveLength(2);
    expect(screen.queryByTestId("standard-colours")).toBeNull();
  });

  it("gives an operator Audit but not the mode select", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "op@example.com", name: "Op", tier: "operator" } });
    renderPage();
    const rows = await screen.findAllByTestId("standard-repo-row");
    expect(screen.queryByTestId("standard-mode-select")).toBeNull();
    expect(within(rows[0]).getByTestId("standard-audit-button")).toBeEnabled();
  });

  it("gives an operate-scope key Audit but not the mode select (#101)", async () => {
    // The audit route takes any operate-or-above credential; the mode is admin-only.
    useAuthStore.setState({ authMode: "key", user: null, keyTier: "operator" });
    renderPage();
    const rows = await screen.findAllByTestId("standard-repo-row");
    expect(screen.queryByTestId("standard-mode-select"), "an operate-scope key is not admin").toBeNull();
    expect(within(rows[0]).getByTestId("standard-audit-button"), "an operate-scope key may audit").toBeEnabled();
  });

  it("gives a read-scope key neither Audit nor the mode select (#101)", async () => {
    useAuthStore.setState({ authMode: "key", user: null, keyTier: "read" });
    renderPage();
    const rows = await screen.findAllByTestId("standard-repo-row");
    expect(screen.queryByTestId("standard-mode-select")).toBeNull();
    expect(within(rows[0]).getByTestId("standard-audit-button"), "a read-scope key cannot POST an audit").toBeDisabled();
  });

  it("links back to the organizations list for an owner with a standard but no organization", async () => {
    api.orgs.mockResolvedValue([org({ owner: "other" })]);
    renderPage();
    const back = await screen.findByTestId("standard-back");
    expect(back).toHaveTextContent("← Organizations");
    expect(back).toHaveAttribute("href", "/orgs");
    expect(screen.getByRole("heading", { level: 1, name: "Design standard" })).toBeInTheDocument();
    // No GET /orgs/<owner>, which would answer 404 for this owner on every load.
    expect(api.org).not.toHaveBeenCalled();
  });

  it("finds an unfinished audit in the jobs list, not by reading each job", async () => {
    api.standards.mockResolvedValue(
      ownerStandards({ repos: [state("acme/low", { mode: "advise", last_audit_job_id: "job-1" })] }),
    );
    api.jobs.mockImplementation(async ({ status }: { status: string }) => ({
      jobs: status === "running" ? [{ job_id: "job-1", status: "running" }] : [],
      total: 0,
      offset: 0,
      limit: 200,
    }));
    renderPage();
    const job = await screen.findByTestId("standard-audit-job");
    expect(await within(job).findByText("Running")).toBeInTheDocument();
    expect(screen.getByTestId("standard-audit-button")).toBeDisabled();
    expect(api.jobs).toHaveBeenCalledTimes(4);
    expect(api.job).not.toHaveBeenCalled();
  });

  it("offers a retry when the standards do not load", async () => {
    api.standards.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText("Failed to load the standards of acme")).toBeInTheDocument();
  });
});

describe("StandardsPage — the first paint", () => {
  // Each answer sizes something above the standard's parts; painted before
  // it, the parts were pushed down when it landed.
  const sources = {
    "the registry": () => api.repos,
    "the organizations list": () => api.orgs,
  };

  it.each(Object.keys(sources))("holds the page until %s is in, then paints it whole", async (name) => {
    const fetcher = sources[name as keyof typeof sources]();
    const answer = fetcher.getMockImplementation() as () => Promise<unknown>;
    let release: () => void = () => {};
    fetcher.mockReturnValue(new Promise<void>((resolve) => (release = resolve)).then(answer));
    renderPage();

    await waitFor(() => expect(api.standards).toHaveBeenCalledWith("acme"));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(screen.queryByTestId("standards-page")).toBeNull();

    await act(async () => release());
    expect(await screen.findByTestId("standards-page")).toBeInTheDocument();
    expect(screen.getAllByTestId("standard-repo-row")).toHaveLength(1);
    expect(screen.getByTestId("standard-back")).toHaveTextContent("← Acme Inc");
  });
});
