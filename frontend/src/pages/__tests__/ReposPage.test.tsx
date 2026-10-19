import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepos = vi.fn();
const mockRegisterRepo = vi.fn();
vi.mock("@/api/repos", () => ({
  fetchRepos: (...a: unknown[]) => mockFetchRepos(...a),
  registerRepo: (...a: unknown[]) => mockRegisterRepo(...a),
  fetchRepoDetail: () => Promise.reject({ response: { status: 404 } }),
}));

const mockFetchDiscoverable = vi.fn();
vi.mock("@/api/github", () => ({
  fetchGitHubRepos: vi.fn(),
  fetchDiscoverableRepos: (...a: unknown[]) => mockFetchDiscoverable(...a),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ReposPage } from "../ReposPage";

const REGISTERED = [
  {
    repo: "acme/registered",
    active: true,
    notes: "",
    created_at: "",
    updated_at: "",
    forge: "github" as const,
    has_qa_config: true,
    docker_access: false,
    env_var_count: 0,
    env_secret_count: 0,
  },
];

function discoverable(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    repos: [
      {
        full_name: "acme/discoverable",
        description: null,
        private: true,
        html_url: "https://github.com/acme/discoverable",
        default_branch: "main",
        language: null,
        open_issues_count: 0,
        token_env_key: "GITHUB_TOKEN__ACME",
        push: true,
      },
      {
        full_name: "octo/no-push",
        description: null,
        private: false,
        html_url: "https://github.com/octo/no-push",
        default_branch: "main",
        language: null,
        open_issues_count: 0,
        token_env_key: "GITHUB_TOKEN",
        push: false,
      },
    ],
    truncated_env_keys: [],
    cached: false,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/repos"]}>
        <Routes>
          <Route path="/repos" element={<ReposPage />} />
          <Route path="/repos/onboard" element={<div data-testid="onboard-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchRepos.mockReset().mockResolvedValue(REGISTERED);
  mockRegisterRepo.mockReset();
  mockFetchDiscoverable.mockReset().mockResolvedValue(discoverable());
});

describe("ReposPage (RAV-1301)", () => {
  it("lists the registry and the discoverable dropdown with env-key labels", async () => {
    renderPage();
    const rows = await screen.findAllByTestId("registered-repo-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("acme/registered");

    const select = await screen.findByTestId("add-repo-select");
    const labels = [...select.querySelectorAll("option")].map((o) => o.textContent);
    expect(labels).toContain("acme/discoverable (private, GITHUB_TOKEN__ACME)");
    // "push?" read as a question; false is a known answer.
    expect(labels).toContain("octo/no-push (public, GITHUB_TOKEN, no push)");
  });

  it("lays the registry out as a table with a head row, like every record list", async () => {
    renderPage();
    const list = await screen.findByTestId("registered-repos-list");
    const heads = within(list).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Repository", "Forge", "QA config", "Actions"]);
    const row = within(list).getByTestId("registered-repo-row");
    expect(row.tagName).toBe("TR");
    expect(within(row).getByRole("link", { name: "Config" })).toHaveAttribute(
      "href",
      "/qa/repos/acme%2Fregistered/config",
    );
  });

  it("points to Organizations in one line", async () => {
    renderPage();
    const pointer = await screen.findByTestId("repos-orgs-pointer");
    expect(pointer.querySelector("a")).toHaveAttribute("href", "/orgs");
  });

  it("flags a repo an admin granted Docker access, and only that one (#522)", async () => {
    mockFetchRepos.mockResolvedValue([
      ...REGISTERED,
      { ...REGISTERED[0], repo: "acme/compose-app", docker_access: true },
    ]);
    renderPage();
    const rows = await screen.findAllByTestId("registered-repo-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).not.toHaveTextContent("Docker access");
    expect(rows[1]).toHaveTextContent("Docker access");
  });

  it("shows the push advisory for a push=false selection", async () => {
    renderPage();
    // Wait for the async discovery options before changing the native select —
    // setting a value with no matching option silently resets to "".
    await screen.findByRole("option", { name: /octo\/no-push/ });
    fireEvent.change(screen.getByTestId("add-repo-select"), {
      target: { value: "octo/no-push" },
    });
    expect(await screen.findByTestId("add-repo-push-advisory")).toBeInTheDocument();
  });

  it("registers a selected repo", async () => {
    mockRegisterRepo.mockResolvedValue({ repo: "acme/discoverable" });
    renderPage();
    await screen.findByRole("option", { name: /acme\/discoverable/ });
    fireEvent.change(screen.getByTestId("add-repo-select"), {
      target: { value: "acme/discoverable" },
    });
    fireEvent.click(screen.getByTestId("add-repo-register"));
    await waitFor(() => expect(mockRegisterRepo).toHaveBeenCalledWith("acme/discoverable", "", "github"));
  });

  it("register & onboard navigates to the wizard pre-seeded", async () => {
    mockRegisterRepo.mockResolvedValue({ repo: "acme/discoverable" });
    renderPage();
    await screen.findByRole("option", { name: /acme\/discoverable/ });
    fireEvent.change(screen.getByTestId("add-repo-select"), {
      target: { value: "acme/discoverable" },
    });
    fireEvent.click(screen.getByTestId("add-repo-register-onboard"));
    expect(await screen.findByTestId("onboard-page")).toBeInTheDocument();
  });

  it("manual entry is marked unverified and validates owner/name shape", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-repo-manual-toggle"));
    const input = screen.getByTestId("add-repo-manual-input");
    expect(screen.getByText(/Unverified/)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "not-a-repo" } });
    expect(screen.getByTestId("add-repo-register")).toBeDisabled();

    fireEvent.change(input, { target: { value: "acme/manual" } });
    expect(screen.getByTestId("add-repo-register")).toBeEnabled();
  });

  it("surfaces truncation honestly", async () => {
    mockFetchDiscoverable.mockResolvedValue(
      discoverable({ truncated_env_keys: ["GITHUB_TOKEN__ACME"] }),
    );
    renderPage();
    expect(await screen.findByTestId("add-repo-truncation-notice")).toHaveTextContent(
      "GITHUB_TOKEN__ACME",
    );
  });
});
