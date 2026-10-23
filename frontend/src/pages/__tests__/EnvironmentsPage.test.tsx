import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepos = vi.fn();

vi.mock("@/api/repos", () => ({
  fetchRepos: () => mockFetchRepos(),
}));

let globalVars: EnvVar[] = [];
let repoVars: EnvVar[] = [];
const mockSetGlobal = vi.fn();
const mockSetRepo = vi.fn();
vi.mock("@/hooks/useEnvironments", () => ({
  useGlobalEnv: () => ({ data: globalVars, isLoading: false }),
  useSetGlobalEnv: () => ({ mutate: mockSetGlobal }),
  useRepoEnv: () => ({ data: repoVars, isLoading: false }),
  useSetRepoEnv: () => ({ mutate: mockSetRepo }),
  useRevealSecret: () => ({ mutateAsync: vi.fn() }),
}));

// DetectBanner and RepoPreferencesSection make their own network calls —
// stub them out; this test is about discoverability, not those panels.
vi.mock("@/components/environments/DetectBanner", () => ({
  DetectBanner: () => null,
}));
vi.mock("@/components/environments/RepoPreferencesSection", () => ({
  RepoPreferencesSection: () => null,
}));
vi.mock("@/components/environments/PullRequestReviewCard", () => ({
  PullRequestReviewCard: () => null,
}));

import { EnvironmentsPage } from "../EnvironmentsPage";
import type { EnvVar } from "@/lib/types/environment";

const REPOS = [
  {
    repo: "acme/backend",
    active: true,
    notes: "",
    created_at: "",
    updated_at: "",
    forge: "github" as const,
    has_qa_config: true,
    env_var_count: 2,
    env_secret_count: 2,
  },
  {
    repo: "acme/console",
    active: true,
    notes: "",
    created_at: "",
    updated_at: "",
    forge: "github" as const,
    has_qa_config: false,
    env_var_count: 22,
    env_secret_count: 20,
  },
];

function renderPage(path = "/environments") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <EnvironmentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  globalVars = [];
  repoVars = [];
  mockFetchRepos.mockResolvedValue(REPOS);
});

describe("EnvironmentsPage — RAV-1294 discoverability", () => {
  it("paints the summary and the sections together, so neither pushes the other", async () => {
    let answer: (repos: unknown) => void = () => {};
    mockFetchRepos.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    renderPage();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("heading", { name: "App config" })).not.toBeInTheDocument();
    answer(REPOS);
    expect(await screen.findByTestId("repo-env-summary")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "App config" })).toBeInTheDocument();
  });

  it("surfaces repo-scoped variables from the Global landing tab", async () => {
    renderPage();
    const summary = await screen.findByTestId("repo-env-summary");
    expect(summary).toHaveTextContent("24 repository-scoped variables across 2 repositories");
    expect(
      within(summary).getByRole("button", { name: "acme/console (22)" }),
    ).toBeInTheDocument();
  });

  it("names its sections and buttons in sentence case and keeps the empty hints readable", async () => {
    renderPage();
    // The Global tab paints once the registry has answered.
    expect(await screen.findByRole("heading", { name: "App config" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "QA test credentials" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add variable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add QA variable" })).toBeInTheDocument();
    const hint = screen.getByText(/to set global defaults/);
    expect(hint.className).toContain("text-fg-muted");
    expect(hint.className).not.toContain("white/[0.12]");
  });

  it("clicking a repo chip jumps to that repo's tab", async () => {
    renderPage();
    const summary = await screen.findByTestId("repo-env-summary");
    await userEvent.click(
      within(summary).getByRole("button", { name: "acme/console (22)" }),
    );
    // The repo tab is active with the repo preselected in the picker.
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Repository" })).toHaveValue(
        "acme/console",
      ),
    );
  });

  it("offers a registry-fed picker instead of a free-text box", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Repository" }));
    const picker = await screen.findByRole("combobox", { name: "Repository" });
    expect(
      within(picker).getByRole("option", { name: "acme/backend (2 vars, 2 secrets)" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("owner/repo")).not.toBeInTheDocument();
  });
});

const variable = (key: string, var_type: EnvVar["var_type"]): EnvVar => ({
  key,
  value: var_type === "secret" ? "" : "info",
  var_type,
  description: "",
  required: false,
  scope: "app",
});

describe("EnvironmentsPage removing a value", () => {
  it("asks before removing a repository value, and a cancel keeps the row and sends nothing", async () => {
    repoVars = [variable("LOG_LEVEL", "config")];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage("/environments?repo=acme%2Fbackend");
    await userEvent.click(await screen.findByRole("button", { name: "Delete variable" }));
    expect(confirmSpy).toHaveBeenCalledWith(
      "Remove LOG_LEVEL from acme/backend? Jobs that start after this will not get it.",
    );
    expect(mockSetRepo).not.toHaveBeenCalled();
    expect(screen.getByText("LOG_LEVEL")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("removes a repository value after the confirm", async () => {
    repoVars = [variable("LOG_LEVEL", "config"), variable("API_URL", "config")];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage("/environments?repo=acme%2Fbackend");
    const row = (await screen.findByText("LOG_LEVEL")).closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Delete variable" }));
    expect(mockSetRepo).toHaveBeenCalledTimes(1);
    expect(mockSetRepo.mock.calls[0][0]).toEqual({
      fullRepo: "acme/backend",
      variables: [{ ...variable("API_URL", "config") }],
    });
    confirmSpy.mockRestore();
  });

  it("says a removed secret cannot be recovered", async () => {
    repoVars = [variable("API_TOKEN", "secret")];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage("/environments?repo=acme%2Fbackend");
    await userEvent.click(await screen.findByRole("button", { name: "Delete variable" }));
    expect(confirmSpy).toHaveBeenCalledWith(
      "Remove API_TOKEN from acme/backend? Jobs that start after this will not get it. embry0 does not keep secret values, so this one cannot be recovered.",
    );
    confirmSpy.mockRestore();
  });

  it("asks before removing a global value, and a cancel sends nothing", async () => {
    globalVars = [variable("SHARED_TOKEN", "secret")];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Delete variable" }));
    expect(confirmSpy).toHaveBeenCalledWith(
      "Remove SHARED_TOKEN for every repository? Jobs that start after this will not get it. embry0 does not keep secret values, so this one cannot be recovered.",
    );
    expect(mockSetGlobal).not.toHaveBeenCalled();
    expect(screen.getByText("SHARED_TOKEN")).toBeInTheDocument();

    confirmSpy.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete variable" }));
    expect(mockSetGlobal).toHaveBeenCalledWith([], expect.anything());
    confirmSpy.mockRestore();
  });
});

describe("EnvironmentsPage Repository tab", () => {
  it("says repository values are for this repository only and win over global ones", async () => {
    renderPage("/environments?repo=acme%2Fbackend");
    expect(
      await screen.findByText(
        "Settings and passwords handed to embry0's test computers for this repository only. A value here wins over a global value with the same name.",
      ),
    ).toBeInTheDocument();
  });
});
