import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The organization page spends its one gold on Apply, the action that
// changes things. Need counts and action chips are labels, so they are
// neutral; a running discovery is a live state, so it reads in blue. The
// token an organization reads from is an identifier, so it is mono, as on
// the /orgs list.
const api = { org: vi.fn(), runs: vi.fn(), repos: vi.fn(), needs: vi.fn() };
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return {
    ...actual,
    fetchOrganization: (...a: unknown[]) => api.org(...a),
    fetchOrganizationRuns: (...a: unknown[]) => api.runs(...a),
    fetchOrganizationRepos: (...a: unknown[]) => api.repos(...a),
    fetchOrganizationNeeds: (...a: unknown[]) => api.needs(...a),
  };
});
vi.mock("@/api/credentials", () => ({ fetchCredentials: () => Promise.resolve([]) }));

import { useAuthStore } from "@/stores/authStore";
import { OrganizationPage } from "../OrganizationPage";
import { DiscoveryStatus } from "@/components/orgs/DiscoveryStatus";
import { need, needs, org, repo, run } from "@/components/orgs/__tests__/fixtures";

const GOLD = /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/;

/** Every element drawn in the gold: a gold fill, a gold wash or gold text. */
function goldElements(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));
}

function describeEls(els: HTMLElement[]): string[] {
  return els.map((el) => `${el.tagName.toLowerCase()} "${el.textContent?.trim() ?? ""}"`);
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(path = "/orgs/acme") {
  return render(
    <QueryClientProvider client={client()}>
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
  api.repos.mockResolvedValue([repo("widgets"), repo("gadgets")]);
  api.needs.mockResolvedValue(
    needs([
      need("n1", "not_registered", ["acme/widgets"]),
      need("n2", "no_webhook", ["acme/widgets", "acme/gadgets"]),
      need("n3", "not_watched", ["acme/gadgets"]),
    ]),
  );
});

describe("OrganizationPage golden touch", () => {
  it("draws only Apply in gold: need counts and action chips are neutral", async () => {
    renderPage();
    expect(await screen.findAllByTestId("org-act-need")).toHaveLength(3);
    expect(await screen.findAllByTestId("org-need-group")).toHaveLength(3);
    await screen.findAllByTestId("org-repo-row");

    const gold = goldElements(screen.getByTestId("org-page"));
    expect(describeEls(gold)).toEqual(['button "Apply"']);
    expect(gold[0]).toBe(screen.getByTestId("org-act-apply-button"));
  });

  it("reads a running discovery in the live-status blue, not the gold", () => {
    render(
      <QueryClientProvider client={client()}>
        <DiscoveryStatus owner="acme" run={run({ status: "running", repos_seen: 1, repos_total: 2 })} />
      </QueryClientProvider>,
    );
    const running = screen.getByTestId("discovery-running");
    expect(within(running).getByText("Running").className).toContain("text-info");
    expect(describeEls(goldElements(running))).toEqual([]);
  });
});

describe("OrganizationPage header", () => {
  it("prints the environment token key in mono, as the /orgs list does", async () => {
    renderPage();
    const meta = await screen.findByTestId("org-meta");
    expect(meta.textContent).toMatch(/^Environment token\s*GITHUB_TOKEN__ACME · 12 repositories/);
    expect(meta.textContent).not.toContain("environment (");
    expect(within(meta).getByText("GITHUB_TOKEN__ACME").className).toContain("font-mono");
  });

  it("prints a stored credential's name in mono", async () => {
    api.org.mockResolvedValue(org({ credential_id: "cred-1" }));
    renderPage();
    const meta = await screen.findByTestId("org-meta");
    expect(within(meta).getByText("cred-1").className).toContain("font-mono");
  });
});
