import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router";
import { AxiosError, AxiosHeaders } from "axios";
import type { BootstrapResult } from "@/api/bootstrap";

const mockMutate = vi.fn();
const mockReset = vi.fn();
let mockIsPending = false;
vi.mock("@/hooks/useBootstrap", () => ({
  useBootstrapRepo: () => ({ mutate: mockMutate, reset: mockReset, isPending: mockIsPending }),
}));

vi.mock("@/hooks/useGitHub", () => ({
  useGitHubRepos: () => ({ data: { repos: [{ full_name: "owner/name" }] } }),
}));

vi.mock("@/hooks/useSandboxProfiles", () => ({
  useSandboxProfiles: () => ({ data: [{ name: "slim" }, { name: "dev-python" }] }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const REGISTERED = [
  { repo: "owner/name", forge: "github", has_qa_config: false },
  { repo: "group/sub/proj", forge: "gitlab", has_qa_config: true },
];
vi.mock("@/hooks/useRepos", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useRepos")>()),
  useRegisteredRepos: () => ({ data: REGISTERED, isLoading: false }),
}));

const mockCreateJob = vi.fn();
let mockCreatePending = false;
vi.mock("@/hooks/useJobs", () => ({
  useCreateJob: () => ({ mutate: mockCreateJob, isPending: mockCreatePending }),
}));

import { OnboardRepoPage } from "../OnboardRepoPage";

const RESULT: BootstrapResult = {
  repo: "owner/name",
  default_branch: "main",
  private: true,
  qa_config_path: "/store/owner__name/qa.yaml",
  qa_required: "auto",
  app: "web",
  sandbox_profile: null,
  steps: ["GitHub connection verified (default branch: main, private: True)", "qa.yaml v2 registered"],
};

function make400(detail: string): AxiosError {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("Request failed with status code 400", "400", config, undefined, {
    data: { detail },
    status: 400,
    statusText: "",
    headers: {},
    config,
  } as never);
}

function JobPageProbe() {
  const { jobId } = useParams();
  return <p data-testid="job-page">job {jobId}</p>;
}

function renderPage(query = "") {
  return render(
    <MemoryRouter initialEntries={[`/repos/onboard${query}`]}>
      <Routes>
        <Route path="/repos/onboard" element={<OnboardRepoPage />} />
        <Route path="/jobs/:jobId" element={<JobPageProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

// RepoSelector propagates its free-text value to the parent on blur (after a
// 150ms close timeout), so type + blur + wait for the submit gate to open.
async function setRepoInput(value: string) {
  const input = screen.getByPlaceholderText("owner/name");
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
  await waitFor(() => expect(screen.getByTestId("bootstrap-submit")).toBeEnabled());
}

// The by-hand form is the second tab; the agent path is the default.
function renderManual(query = "") {
  const view = renderPage(query);
  fireEvent.click(screen.getByRole("tab", { name: "Set up by hand" }));
  return view;
}

beforeEach(() => {
  mockMutate.mockReset();
  mockReset.mockReset();
  mockIsPending = false;
  mockCreateJob.mockReset();
  mockCreatePending = false;
});

describe("OnboardRepoPage — onboarding agent", () => {
  it("opens on the agent path, with the by-hand form as the second tab", () => {
    renderPage();
    expect(screen.getByRole("tab", { name: "Analyze with the onboarding agent" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("onboard-agent-submit")).toBeInTheDocument();
    expect(screen.queryByTestId("bootstrap-submit")).toBeNull();
  });

  it("names the page for setting up testing, not for GitHub", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Set up testing");
    expect(screen.queryByText(/GitHub repo/)).toBeNull();
    expect(screen.queryByText(/GitHub token/)).toBeNull();
  });

  it("offers every registered repo, GitLab included, labelled with its forge", () => {
    renderPage();
    const picker = screen.getByLabelText("Repository");
    expect(picker).toHaveTextContent("owner/name · GitHub");
    expect(picker).toHaveTextContent("group/sub/proj · GitLab");
  });

  it("prefills the agent's repo from the ?repo= query param", () => {
    renderPage("?repo=group%2Fsub%2Fproj");
    expect(screen.getByLabelText("Repository")).toHaveValue("group/sub/proj");
  });

  it("starts the onboard job on the repo's own branch and opens its job page", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "group/sub/proj" } });
    mockCreateJob.mockImplementationOnce((_req, { onSuccess }) => onSuccess({ job_id: "job-9" }));
    fireEvent.click(screen.getByTestId("onboard-agent-submit"));

    expect(mockCreateJob).toHaveBeenCalledWith(
      { pipeline: "onboard", repo: "group/sub/proj", skip_smoke: false },
      expect.anything(),
    );
    expect(await screen.findByTestId("job-page")).toHaveTextContent("job job-9");
  });

  it("sends the branch and the skip-smoke toggle when set", () => {
    renderPage("?repo=owner%2Fname");
    fireEvent.change(screen.getByLabelText("Branch"), { target: { value: "  dev  " } });
    fireEvent.click(screen.getByLabelText("Skip the smoke boot"));
    fireEvent.click(screen.getByTestId("onboard-agent-submit"));

    expect(mockCreateJob).toHaveBeenCalledWith(
      { pipeline: "onboard", repo: "owner/name", branch: "dev", skip_smoke: true },
      expect.anything(),
    );
  });

  it("cannot start without a repo", () => {
    renderPage();
    expect(screen.getByTestId("onboard-agent-submit")).toBeDisabled();
  });

  it("warns that a stored config will be replaced", () => {
    renderPage("?repo=group%2Fsub%2Fproj");
    expect(screen.getByTestId("onboard-agent-replaces-config")).toHaveTextContent("already has a stored QA config");
    fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "owner/name" } });
    expect(screen.queryByTestId("onboard-agent-replaces-config")).toBeNull();
  });

  it("shows why the job could not be started", () => {
    renderPage("?repo=owner%2Fname");
    mockCreateJob.mockImplementationOnce((_req, { onError }) => onError(make400("executor not initialized")));
    fireEvent.click(screen.getByTestId("onboard-agent-submit"));
    expect(screen.getByTestId("onboard-agent-error")).toHaveTextContent("executor not initialized");
    expect(screen.queryByTestId("job-page")).toBeNull();
  });

  it("opens the by-hand form directly with ?mode=manual", () => {
    renderPage("?repo=owner%2Fname&mode=manual");
    expect(screen.getByTestId("bootstrap-submit")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("owner/name");
  });
});

describe("OnboardRepoPage — by-hand form", () => {
  it("prefills the repo from the ?repo= query param", () => {
    renderManual("?repo=owner%2Fname");
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("owner/name");
  });

  it("rejects a malformed repo without calling the backend", async () => {
    renderManual();
    await setRepoInput("not-a-repo");
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-field-error")).toHaveTextContent("owner/name form");
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("rejects an invalid app slug without calling the backend", async () => {
    renderManual();
    await setRepoInput("owner/name");
    fireEvent.change(screen.getByLabelText("App name"), { target: { value: "Not A Slug" } });
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-field-error")).toHaveTextContent("lowercase slug");
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("submits the trimmed form values", async () => {
    renderManual();
    await setRepoInput("  owner/name  ");
    fireEvent.change(screen.getByLabelText("App name"), { target: { value: "web" } });
    fireEvent.change(screen.getByLabelText("Boot command"), { target: { value: "npm run dev" } });
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ repo: "owner/name", app: "web", boot_command: "npm run dev", force: false }),
      expect.anything(),
    );
  });

  it("names itself the by-hand path and points most people at the repository page", () => {
    renderManual();
    expect(
      screen.getByText(
        "Fill in how embry0 starts your app. Most people should use 'Let embry0 set up testing' on the repository page instead.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Onboard a repo|qa\.yaml|external config store/)).not.toBeInTheDocument();
    expect(screen.getByTestId("bootstrap-submit")).toHaveTextContent("Save test setup");
  });

  it("says it takes GitHub repositories only and points GitLab at the agent", () => {
    renderManual();
    expect(screen.getByText(/takes GitHub repositories only/)).toHaveTextContent("onboarding agent");
  });

  it("calls the work machine by its plain name, with embry0's default first", () => {
    renderManual();
    const machine = screen.getByLabelText("Work machine") as HTMLSelectElement;
    expect(machine.options[0].textContent).toBe("embry0's default");
    expect(screen.queryByText(/triage/)).not.toBeInTheDocument();
    expect(screen.queryByText("Sandbox profile")).not.toBeInTheDocument();
  });

  it.each([
    ["the current server", "a QA config for 'owner/name' already exists in the embry0 DB — pass force=true / --force to overwrite it"],
    [
      "a server that keeps History",
      "This repository already has a test setup. Replacing it keeps the current one in History (send force=true, or --force on the command line).",
    ],
  ])("offers Replace setup when %s says a setup exists, and re-submits with force", async (_server, detail) => {
    renderManual();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onError }) => onError(make400(detail)));
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    const panel = screen.getByTestId("bootstrap-force-panel");
    expect(panel).toHaveTextContent(
      "This repository already has a test setup. Replacing it keeps the current one in History, so you can undo it.",
    );
    expect(document.body.textContent).not.toMatch(/force=true|--force/);
    expect(screen.queryByRole("button", { name: "Overwrite" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Replace setup" }));
    expect(mockMutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ repo: "owner/name", force: true }),
      expect.anything(),
    );
  });

  it("shows other 400 details as a plain inline error", async () => {
    renderManual();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onError }) =>
      onError(make400("no GitHub token configured for owner 'owner'")),
    );
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-error")).toHaveTextContent("no GitHub token");
    expect(screen.queryByTestId("bootstrap-force-panel")).not.toBeInTheDocument();
  });

  it("says the test setup is saved and leads back to the repository first", async () => {
    renderManual();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onSuccess }) => onSuccess(RESULT));
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Test setup saved");
    const steps = screen.getByTestId("bootstrap-steps");
    expect(steps).toHaveTextContent("GitHub connection verified");
    expect(steps).toHaveTextContent("qa.yaml v2 registered");
    // The raw store path means nothing to the person reading this.
    expect(screen.queryByText(RESULT.qa_config_path)).not.toBeInTheDocument();

    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveTextContent("Back to the repository");
    expect(links[0]).toHaveAttribute("href", "/repos/owner/name");
    expect(screen.getByTestId("bootstrap-view-qa-cta")).toHaveAttribute("href", "/qa/repos/owner%2Fname");
    expect(screen.getByTestId("bootstrap-view-qa-cta")).toHaveTextContent("View test runs");
    expect(screen.queryByText("Edit QA config")).not.toBeInTheDocument();
    expect(links.filter((a) => a.getAttribute("href") === "/repos/owner/name")).toHaveLength(1);
  });

  it("goes back to a GitLab subgroup repository by its full path", async () => {
    renderManual();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onSuccess }) =>
      onSuccess({ ...RESULT, repo: "acme/group/widgets" }),
    );
    fireEvent.click(screen.getByTestId("bootstrap-submit"));
    expect(screen.getByRole("link", { name: "Back to the repository" })).toHaveAttribute(
      "href",
      "/repos/acme/group/widgets",
    );
  });

  it("back-links to the central /repos page (RAV-1301)", () => {
    renderPage();
    expect(screen.getByRole("link", { name: "← Repos" })).toHaveAttribute("href", "/repos");
  });

  it("resets back to a blank by-hand form from the success screen", async () => {
    renderManual();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onSuccess }) => onSuccess(RESULT));
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    fireEvent.click(screen.getByText("Set up another repository"));
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("");
    expect(mockReset).toHaveBeenCalled();
  });
});
