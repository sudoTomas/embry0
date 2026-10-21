import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
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

import { toast } from "sonner";
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

/** Stands in for the repo page: shows the path it was opened on. */
function RepoPageProbe() {
  return <div data-testid="repo-page">{useLocation().pathname}</div>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/repos"]}>
        <Routes>
          <Route path="/repos" element={<ReposPage />} />
          <Route path="/repos/onboard" element={<div data-testid="onboard-page" />} />
          <Route path="/repos/*" element={<RepoPageProbe />} />
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
    expect(heads).toEqual(["Repository", "Forge", "Testing", "Actions"]);
    const row = within(list).getByTestId("registered-repo-row");
    expect(row.tagName).toBe("TR");
  });

  it("sends each row's Setup link to the repository page", async () => {
    mockFetchRepos.mockResolvedValue([
      ...REGISTERED,
      { ...REGISTERED[0], repo: "acme/group/widgets", forge: "gitlab" as const },
    ]);
    renderPage();
    const rows = await screen.findAllByTestId("registered-repo-row");
    expect(within(rows[0]).getByRole("link", { name: "Setup" })).toHaveAttribute("href", "/repos/acme/registered");
    expect(within(rows[1]).getByRole("link", { name: "Setup" })).toHaveAttribute("href", "/repos/acme/group/widgets");
    expect(screen.queryByRole("link", { name: "Config" })).not.toBeInTheDocument();
  });

  it("says whether testing is set up, in plain words", async () => {
    mockFetchRepos.mockResolvedValue([
      ...REGISTERED,
      { ...REGISTERED[0], repo: "acme/new", has_qa_config: false },
    ]);
    renderPage();
    const rows = await screen.findAllByTestId("registered-repo-row");
    expect(rows[0]).toHaveTextContent("Test setup saved");
    expect(rows[1]).toHaveTextContent("Testing not set up");
    expect(screen.queryByText(/QA config|Config stored/)).not.toBeInTheDocument();
  });

  it("has no separate onboarding button; the Add panel is on the page", async () => {
    renderPage();
    await screen.findByTestId("registered-repos-list");
    expect(screen.queryByTestId("onboard-repo-cta")).not.toBeInTheDocument();
    expect(screen.queryByText("Onboard repo")).not.toBeInTheDocument();
    expect(screen.getByTestId("add-repo-panel")).toBeInTheDocument();
  });

  it("finds a repository by name, ignoring case", async () => {
    mockFetchRepos.mockResolvedValue([
      ...REGISTERED,
      { ...REGISTERED[0], repo: "acme/widgets" },
      { ...REGISTERED[0], repo: "acme/Gadgets" },
    ]);
    renderPage();
    expect(await screen.findAllByTestId("registered-repo-row")).toHaveLength(3);
    const find = screen.getByPlaceholderText("Find a repository");

    fireEvent.change(find, { target: { value: "GADG" } });
    const rows = screen.getAllByTestId("registered-repo-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("acme/Gadgets");

    fireEvent.change(find, { target: { value: "nothing-here" } });
    expect(screen.queryAllByTestId("registered-repo-row")).toHaveLength(0);
    expect(screen.getByTestId("registry-no-match")).toHaveTextContent('No repository matches "nothing-here".');

    fireEvent.change(find, { target: { value: "" } });
    expect(screen.getAllByTestId("registered-repo-row")).toHaveLength(3);
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

  it("registers a selected repo and opens its page", async () => {
    mockRegisterRepo.mockResolvedValue({ repo: "acme/discoverable" });
    renderPage();
    await screen.findByRole("option", { name: /acme\/discoverable/ });
    fireEvent.change(screen.getByTestId("add-repo-select"), {
      target: { value: "acme/discoverable" },
    });
    fireEvent.click(screen.getByTestId("add-repo-register"));
    await waitFor(() => expect(mockRegisterRepo).toHaveBeenCalledWith("acme/discoverable", "", "github"));
    expect(await screen.findByTestId("repo-page")).toHaveTextContent("/repos/acme/discoverable");
    expect(toast.success).toHaveBeenCalledWith("acme/discoverable registered");
  });

  it("Register and set up opens the new repository's page, not the form", async () => {
    mockRegisterRepo.mockResolvedValue({ repo: "acme/discoverable" });
    renderPage();
    await screen.findByRole("option", { name: /acme\/discoverable/ });
    fireEvent.change(screen.getByTestId("add-repo-select"), {
      target: { value: "acme/discoverable" },
    });
    const setUp = screen.getByRole("button", { name: "Register and set up" });
    expect(setUp).toHaveAttribute("data-testid", "add-repo-register-onboard");
    fireEvent.click(setUp);
    expect(await screen.findByTestId("repo-page")).toHaveTextContent("/repos/acme/discoverable");
    expect(screen.queryByTestId("onboard-page")).not.toBeInTheDocument();
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
