import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
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

function renderPage(query = "") {
  return render(
    <MemoryRouter initialEntries={[`/repos/onboard${query}`]}>
      <Routes>
        <Route path="/repos/onboard" element={<OnboardRepoPage />} />
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

beforeEach(() => {
  mockMutate.mockReset();
  mockReset.mockReset();
  mockIsPending = false;
});

describe("OnboardRepoPage", () => {
  it("prefills the repo from the ?repo= query param", () => {
    renderPage("?repo=owner%2Fname");
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("owner/name");
  });

  it("rejects a malformed repo without calling the backend", async () => {
    renderPage();
    await setRepoInput("not-a-repo");
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-field-error")).toHaveTextContent("owner/name form");
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("rejects an invalid app slug without calling the backend", async () => {
    renderPage();
    await setRepoInput("owner/name");
    fireEvent.change(screen.getByLabelText("App name"), { target: { value: "Not A Slug" } });
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-field-error")).toHaveTextContent("lowercase slug");
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("submits the trimmed form values", async () => {
    renderPage();
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
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Set up testing by hand");
    expect(
      screen.getByText(
        "Fill in how embry0 starts your app. Most people should use 'Let embry0 set up testing' on the repository page instead.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Onboard a repo|qa\.yaml|external config store/)).not.toBeInTheDocument();
    expect(screen.getByTestId("bootstrap-submit")).toHaveTextContent("Save test setup");
  });

  it("calls the work machine by its plain name, with embry0's default first", () => {
    renderPage();
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
    renderPage();
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
    renderPage();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onError }) =>
      onError(make400("no GitHub token configured for owner 'owner'")),
    );
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    expect(screen.getByTestId("bootstrap-error")).toHaveTextContent("no GitHub token");
    expect(screen.queryByTestId("bootstrap-force-panel")).not.toBeInTheDocument();
  });

  it("says the test setup is saved and leads back to the repository first", async () => {
    renderPage();
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
    renderPage();
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

  it("resets back to a blank form from the success screen", async () => {
    renderPage();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onSuccess }) => onSuccess(RESULT));
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    fireEvent.click(screen.getByText("Set up another repository"));
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("");
    expect(mockReset).toHaveBeenCalled();
  });
});
