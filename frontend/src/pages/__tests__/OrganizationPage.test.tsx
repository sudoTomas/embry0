import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = { org: vi.fn(), runs: vi.fn(), repos: vi.fn(), needs: vi.fn(), discover: vi.fn() };
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return {
    ...actual,
    fetchOrganization: (...a: unknown[]) => api.org(...a),
    fetchOrganizationRuns: (...a: unknown[]) => api.runs(...a),
    fetchOrganizationRepos: (...a: unknown[]) => api.repos(...a),
    fetchOrganizationNeeds: (...a: unknown[]) => api.needs(...a),
    discoverOrganization: (...a: unknown[]) => api.discover(...a),
  };
});
vi.mock("@/api/credentials", () => ({ fetchCredentials: () => Promise.resolve([]) }));
const mockStandards = vi.fn();
vi.mock("@/api/standards", async () => {
  const actual = await vi.importActual<typeof import("@/api/standards")>("@/api/standards");
  return { ...actual, fetchOwnerStandards: (...a: unknown[]) => mockStandards(...a) };
});

import { useAuthStore } from "@/stores/authStore";
import { OrganizationPage } from "../OrganizationPage";
import { need, needs, org, repo, run } from "@/components/orgs/__tests__/fixtures";
import { ownerStandards } from "@/components/standards/__tests__/fixtures";

function renderPage(path = "/orgs/acme", client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/orgs/:owner" element={<OrganizationPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  useAuthStore.setState({ authMode: "key" });
  api.org.mockResolvedValue(org({ discovered_at: "2026-01-01T00:00:00Z" }));
  api.runs.mockResolvedValue([run()]);
  api.repos.mockResolvedValue([repo("widgets")]);
  api.needs.mockResolvedValue(needs([need("n1", "not_registered", ["acme/widgets"])]));
  api.discover.mockResolvedValue(run({ id: "odr-2", status: "running" }));
  mockStandards.mockReset().mockResolvedValue(ownerStandards());
});

describe("OrganizationPage", () => {
  it("shows the latest report, the needs and the act panel", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "acme" })).toBeInTheDocument();
    expect(await screen.findByTestId("discovery-summary")).toBeInTheDocument();
    expect(await screen.findAllByTestId("org-repo-row")).toHaveLength(1);
    expect(await screen.findAllByTestId("org-need-group")).toHaveLength(1);
    expect(screen.getByTestId("org-act-panel")).toBeInTheDocument();
  });

  it("puts what embry0 needs and the act panel above the repository table", async () => {
    renderPage();
    const needGroup = (await screen.findAllByTestId("org-need-group"))[0];
    const actPanel = screen.getByTestId("org-act-panel");
    const repoRow = (await screen.findAllByTestId("org-repo-row"))[0];
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(needGroup, actPanel)).toBe(true);
    expect(follows(actPanel, repoRow)).toBe(true);
  });

  it("has a Design standard section that leads to the Standards view, above the repository table", async () => {
    renderPage();
    const heading = await screen.findByRole("heading", { name: "Design standard" });
    expect(await screen.findByTestId("org-standard-link")).toHaveAttribute("href", "/orgs/acme/standards");
    expect(mockStandards).toHaveBeenCalledWith("acme");
    const repoRow = (await screen.findAllByTestId("org-repo-row"))[0];
    expect(heading.compareDocumentPosition(repoRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the rediscovery schedule in the header", async () => {
    api.org.mockResolvedValue(org({ settings: { rediscover_hours: 24 } }));
    renderPage();
    const setting = await screen.findByTestId("org-rediscover-setting");
    expect((screen.getByTestId("org-rediscover-hours") as HTMLInputElement).value).toBe("24");
    expect(setting.compareDocumentPosition(screen.getByRole("heading", { name: "Latest discovery" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("rediscovers on demand", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("org-rediscover"));
    await waitFor(() => expect(api.discover).toHaveBeenCalledWith("acme"));
  });

  it("keeps working when the server has no needs endpoint", async () => {
    api.needs.mockResolvedValue(null);
    renderPage();
    expect(await screen.findAllByText("This server has no needs endpoint yet")).toHaveLength(2);
    expect(await screen.findAllByTestId("org-repo-row")).toHaveLength(1);
  });

  it("offers the wizard for an owner that is not registered", async () => {
    api.org.mockRejectedValue({ response: { status: 404, data: { detail: "no organization ghost" } } });
    renderPage("/orgs/ghost");
    expect(await screen.findByText("No organization ghost")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add organization" })).toHaveAttribute("href", "/orgs/-/new");
  });
});

describe("OrganizationPage — the first paint", () => {
  // Each answer sizes a section with others below it; painted before it,
  // the section grew when it landed and pushed the rest of the page down.
  const sources = {
    "the latest discovery": () => api.runs,
    "the needs list": () => api.needs,
    "the standard": () => mockStandards,
  };

  it.each(Object.keys(sources))("holds the sections until %s is in, then paints them together", async (name) => {
    const fetcher = sources[name as keyof typeof sources]();
    const answer = fetcher.getMockImplementation() as () => Promise<unknown>;
    let release: () => void = () => {};
    fetcher.mockReturnValue(new Promise<void>((resolve) => (release = resolve)).then(answer));
    renderPage();

    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("acme"));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(screen.queryByTestId("org-page")).toBeNull();

    await act(async () => release());
    expect(await screen.findByTestId("org-page")).toBeInTheDocument();
    expect(screen.getByTestId("discovery-summary")).toBeInTheDocument();
    expect(screen.getAllByTestId("org-need-group")).toHaveLength(1);
    expect(screen.getByTestId("org-standard-summary")).toBeInTheDocument();
  });

  it("does not hold the page through the retries of a section that fails", async () => {
    api.needs.mockRejectedValue(new Error("boom"));
    // One retry, far off: the page paints on the first failed attempt.
    const client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 60_000 } } });
    renderPage("/orgs/acme", client);
    expect(await screen.findByTestId("org-page")).toBeInTheDocument();
    expect(screen.getByTestId("discovery-summary")).toBeInTheDocument();
    expect(api.needs).toHaveBeenCalledTimes(1);
  });
});
