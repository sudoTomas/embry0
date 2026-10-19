import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const mockRegister = vi.fn();
const mockSetRepoCredentials = vi.fn();
const mockFetchRepoDetail = vi.fn();
const mockNavigate = vi.fn();

let mockGithubData: unknown;
let mockGitlabData: unknown;

vi.mock("@/api/github", () => ({
  fetchDiscoverableRepos: vi.fn(),
}));
vi.mock("@/api/gitlab", () => ({
  fetchDiscoverableProjects: vi.fn(),
}));
vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...(actual as object),
    useNavigate: () => mockNavigate,
  };
});
vi.mock("@/api/repos", () => ({
  registerRepo: (...a: unknown[]) => mockRegister(...a),
  fetchRepoDetail: (...a: unknown[]) => mockFetchRepoDetail(...a),
}));
vi.mock("@/api/credentials", () => ({
  setRepoCredentials: (...a: unknown[]) => mockSetRepoCredentials(...a),
}));
vi.mock("@/hooks/useGitHub", () => ({
  useDiscoverableRepos: () => ({ data: mockGithubData, isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useGitLab", () => ({
  useDiscoverableProjects: () => ({ data: mockGitlabData, isLoading: false, isError: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { AddRepoPanel } from "../AddRepoPanel";

const SECRET = "ghp_placeholdersecret";

const GITHUB = {
  repos: [
    {
      full_name: "acme/widgets",
      description: SECRET,
      private: true,
      html_url: "",
      default_branch: "main",
      language: null,
      open_issues_count: 0,
      token_env_key: "GITHUB_TOKEN__ACME",
      credential_id: "cred-000000000001",
      credential_label: "alice",
      push: true,
    },
  ],
  truncated_env_keys: [],
  cached: false,
  failed: [] as string[],
};

const GITLAB = {
  projects: [
    {
      full_name: "acme/products/fbr",
      description: null,
      private: true,
      html_url: "",
      default_branch: "main",
      open_issues_count: 7,
      credential_id: "cred-000000000002",
      credential_label: "alice",
    },
  ],
  cached: false,
  configured: true,
  failed: [] as string[],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AddRepoPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

async function registerSelected(name: string) {
  const user = userEvent.setup();
  await user.selectOptions(screen.getByTestId("add-repo-select"), name);
  await user.click(screen.getByRole("button", { name: /^register$/i }));
}

describe("AddRepoPanel discovery account", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGithubData = GITHUB;
    mockGitlabData = { projects: [], cached: false, configured: false, failed: [] };
    mockRegister.mockResolvedValue({ repo: "acme/widgets", forge: "github" });
    mockSetRepoCredentials.mockResolvedValue({});
    mockFetchRepoDetail.mockRejectedValue({ response: { status: 404 } });
  });

  it("option text contains alice and not GITHUB_TOKEN__ for a registry-discovered repo", () => {
    renderPanel();
    const option = screen.getByRole("option", { name: /acme\/widgets/ });
    expect(option.textContent).toBe("acme/widgets (private, alice)");
    expect(option.textContent).not.toContain("GITHUB_TOKEN__");
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("register succeeds and bind failure is reported", async () => {
    mockSetRepoCredentials.mockRejectedValue({ response: { status: 403 } });
    renderPanel();
    await registerSelected("acme/widgets");
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Registered, but binding the account needs admin scope",
      ),
    );
    expect(mockRegister).toHaveBeenCalledTimes(1);
    expect(mockRegister).toHaveBeenCalledWith("acme/widgets", "", "github");
    expect(toast.success).toHaveBeenCalledWith("acme/widgets registered");
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("bind called with the credential id on success", async () => {
    const { client } = renderPanel();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await registerSelected("acme/widgets");
    await waitFor(() =>
      expect(mockSetRepoCredentials).toHaveBeenCalledWith("acme/widgets", [
        { credential_id: "cred-000000000001", role: "forge" },
      ]),
    );
    expect(mockRegister).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["credentials", "repo", "acme/widgets"],
    });
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("failed-credentials advisory renders", () => {
    mockGithubData = { ...GITHUB, failed: ["revoked"] };
    renderPanel();
    expect(screen.getByTestId("add-repo-failed-credentials").textContent).toBe(
      "Could not list repos with: revoked",
    );
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("binds the gitlab project credential id on register", async () => {
    mockGitlabData = GITLAB;
    mockRegister.mockResolvedValue({ repo: "acme/products/fbr", forge: "gitlab" });
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByTestId("add-repo-forge-gitlab"));
    const option = screen.getByRole("option", { name: /acme\/products\/fbr/ });
    expect(option.textContent).toBe("acme/products/fbr (private, default main, alice)");
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/products/fbr");
    await user.click(screen.getByRole("button", { name: /^register$/i }));
    await waitFor(() =>
      expect(mockSetRepoCredentials).toHaveBeenCalledWith("acme/products/fbr", [
        { credential_id: "cred-000000000002", role: "forge" },
      ]),
    );
    expect(mockRegister).toHaveBeenCalledTimes(1);
    expect(mockRegister).toHaveBeenCalledWith("acme/products/fbr", "", "gitlab");
  });

  it("does not rebind a repo that is already registered", async () => {
    mockFetchRepoDetail.mockResolvedValue({ repo: "acme/widgets" });
    renderPanel();
    await registerSelected("acme/widgets");
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "acme/widgets is already registered; its account bindings were left unchanged",
      ),
    );
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockSetRepoCredentials).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("does not onboard a repo that is already registered", async () => {
    mockFetchRepoDetail.mockResolvedValue({ repo: "acme/widgets" });
    const user = userEvent.setup();
    renderPanel();
    await user.selectOptions(screen.getByTestId("add-repo-select"), "acme/widgets");
    await user.click(screen.getByTestId("add-repo-register-onboard"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "acme/widgets is already registered; its account bindings were left unchanged",
      ),
    );
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockSetRepoCredentials).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("does not register when lookup fails for a reason other than not found", async () => {
    mockFetchRepoDetail.mockRejectedValue({ response: { status: 500 } });
    renderPanel();
    await registerSelected("acme/widgets");
    await waitFor(() =>
      // The reason the request failed, not a guess that the path was wrong.
      expect(toast.error).toHaveBeenCalledWith("Failed to register repo: The request failed (HTTP 500)"),
    );
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockSetRepoCredentials).not.toHaveBeenCalled();
  });
});
