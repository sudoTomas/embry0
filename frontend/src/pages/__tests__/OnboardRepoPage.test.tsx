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

  it("shows the force panel on an already-has-a-setup 400 and re-submits with force", async () => {
    renderPage();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onError }) =>
      onError(
        make400(
          "This repository already has a test setup. Replacing it keeps the current one in History " +
            "(send force=true, or --force on the command line).",
        ),
      ),
    );
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    const panel = screen.getByTestId("bootstrap-force-panel");
    expect(panel).toHaveTextContent("already has a test setup");

    fireEvent.click(screen.getByTestId("bootstrap-force-overwrite"));
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

  it("renders the steps checklist and encoded CTAs on success", async () => {
    renderPage();
    await setRepoInput("owner/name");
    mockMutate.mockImplementationOnce((_req, { onSuccess }) => onSuccess(RESULT));
    fireEvent.click(screen.getByTestId("bootstrap-submit"));

    const steps = screen.getByTestId("bootstrap-steps");
    expect(steps).toHaveTextContent("GitHub connection verified");
    expect(steps).toHaveTextContent("qa.yaml v2 registered");
    expect(screen.getByTestId("bootstrap-edit-config-cta")).toHaveAttribute(
      "href",
      "/qa/repos/owner%2Fname/config",
    );
    expect(screen.getByTestId("bootstrap-view-qa-cta")).toHaveAttribute("href", "/qa/repos/owner%2Fname");
    expect(screen.getByTestId("bootstrap-repo-overview-cta")).toHaveAttribute(
      "href",
      "/repos/owner/name",
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

    fireEvent.click(screen.getByText("Onboard another repo"));
    expect(screen.getByPlaceholderText("owner/name")).toHaveValue("");
    expect(mockReset).toHaveBeenCalled();
  });
});
