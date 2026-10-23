import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";

const mockRegister = vi.fn();

let mockGithubData: unknown;
let mockGitlabData: unknown;

vi.mock("@/api/github", () => ({
  fetchDiscoverableRepos: vi.fn(),
}));
vi.mock("@/api/gitlab", () => ({
  fetchDiscoverableProjects: vi.fn(),
}));
vi.mock("@/api/repos", () => ({
  registerRepo: (...a: unknown[]) => mockRegister(...a),
  fetchRepoDetail: () => Promise.reject({ response: { status: 404 } }),
}));
vi.mock("@/hooks/useGitHub", () => ({
  useDiscoverableRepos: () => ({ data: mockGithubData, isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useGitLab", () => ({
  useDiscoverableProjects: () => ({ data: mockGitlabData, isLoading: false, isError: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AddRepoPanel } from "../AddRepoPanel";

const GITHUB = {
  repos: [
    {
      full_name: "acme/widgets",
      description: null,
      private: true,
      html_url: "",
      default_branch: "main",
      language: null,
      open_issues_count: 0,
      token_env_key: "GITHUB_TOKEN",
      push: true,
    },
  ],
  truncated_env_keys: [],
  cached: false,
};

const GITLAB_PROJECTS = {
  projects: [
    {
      full_name: "acme/products/fbr",
      description: null,
      private: true,
      html_url: "",
      default_branch: "main",
      open_issues_count: 7,
    },
  ],
  cached: false,
  configured: true,
};

/** Where the panel sent the browser. */
function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/repos"]}>
        <AddRepoPanel />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AddRepoPanel forge selection (#169)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGithubData = GITHUB;
    mockGitlabData = GITLAB_PROJECTS;
    mockRegister.mockResolvedValue({ repo: "x", forge: "github" });
  });

  it("hides the forge toggle on a GitHub-only deployment", () => {
    // No GITLAB_TOKEN configured — showing a GitLab option that can only fail
    // is worse than not offering it.
    mockGitlabData = { projects: [], cached: false, configured: false };
    renderPanel();
    expect(screen.queryByTestId("add-repo-forge-toggle")).toBeNull();
  });

  it("shows the toggle once GitLab is configured", () => {
    renderPanel();
    expect(screen.getByTestId("add-repo-forge-toggle")).toBeInTheDocument();
  });

  it("lists GitLab projects, including a nested subgroup path", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByTestId("add-repo-forge-gitlab"));
    expect(
      await screen.findByRole("option", { name: /acme\/products\/fbr/ }),
    ).toBeInTheDocument();
  });

  it("registers the selected project against the gitlab forge", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByTestId("add-repo-forge-gitlab"));
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/products/fbr");
    await user.click(screen.getByRole("button", { name: /^register$/i }));
    await waitFor(() => expect(mockRegister).toHaveBeenCalled());
    expect(mockRegister).toHaveBeenCalledWith("acme/products/fbr", "", "gitlab");
  });

  it("Register and set up on a GitLab subgroup path lands on that repository's page", async () => {
    // The old target, the setup form, only knows GitHub; the repo page works for both.
    mockRegister.mockResolvedValue({ repo: "acme/products/fbr", forge: "gitlab" });
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByTestId("add-repo-forge-gitlab"));
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/products/fbr");
    await user.click(screen.getByRole("button", { name: "Register and set up" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/repos/acme/products/fbr"));
    expect(screen.getByTestId("where").textContent).not.toContain("onboard");
  });

  it("Register lands on the new repository's page too", async () => {
    mockRegister.mockResolvedValue({ repo: "acme/widgets", forge: "github" });
    const user = userEvent.setup();
    renderPanel();
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/widgets");
    await user.click(screen.getByRole("button", { name: /^register$/i }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/repos/acme/widgets"));
  });

  it("switching forge clears a stale selection", async () => {
    // Otherwise a GitHub repo could be registered against the gitlab forge.
    const user = userEvent.setup();
    renderPanel();
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/widgets");
    await user.click(screen.getByTestId("add-repo-forge-gitlab"));
    expect((screen.getByTestId("add-repo-select") as HTMLSelectElement).value).toBe("");
  });
});
