import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

const mockUseRepoPreferences = vi.fn();
const mockMutate = vi.fn();
const mockDeleteMutate = vi.fn();

vi.mock("@/hooks/useRepoPreferences", () => ({
  useRepoPreferences: (...a: unknown[]) => mockUseRepoPreferences(...a),
  useSetRepoPreferences: () => ({ mutate: mockMutate, isPending: false }),
  useDeleteRepoPreferences: () => ({ mutate: mockDeleteMutate, isPending: false }),
}));

vi.mock("@/hooks/useSandboxProfiles", () => ({
  useSandboxProfiles: () => ({ data: [{ name: "python-3.12" }], isLoading: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { RepoPreferencesSection } from "../RepoPreferencesSection";

const PREFS = {
  repo: "acme/group/backend",
  sandbox_profile: "python-3.12",
  language_hint: "python",
  notes: "",
  execution_mode: null,
  auth_mode: null,
  git_author_name: null,
  git_author_email: null,
  dev_checks: [],
  base_branch: "dev",
  updated_at: "2026-09-18T00:00:00Z",
};

function renderSection() {
  // The section links to the QA config page, so it needs a router context.
  return render(
    <MemoryRouter>
      <RepoPreferencesSection repo="acme/group/backend" />
    </MemoryRouter>,
  );
}

describe("RepoPreferencesSection base branch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({ data: PREFS, isLoading: false });
  });

  it("shows the stored base branch", () => {
    renderSection();
    expect(screen.getByLabelText("Base branch")).toHaveValue("dev");
  });

  it("saves an edited base branch", async () => {
    const user = userEvent.setup();
    renderSection();
    const input = screen.getByLabelText("Base branch");
    await user.clear(input);
    await user.type(input, "release/2026.09");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update.base_branch).toBe("release/2026.09");
  });

  it("leaves out the fields it does not edit, so a save cannot overwrite them (#350)", async () => {
    const user = userEvent.setup();
    renderSection();
    await user.clear(screen.getByLabelText("Base branch"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    const update = mockMutate.mock.calls[0][0].update;
    expect(Object.keys(update)).not.toContain("execution_mode");
    expect(Object.keys(update)).not.toContain("auth_mode");
    expect(Object.keys(update)).not.toContain("verification");
  });

  it("sends null when the field is cleared", async () => {
    const user = userEvent.setup();
    renderSection();
    await user.clear(screen.getByLabelText("Base branch"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update.base_branch).toBeNull();
  });

  it("renders empty when the repo has no preferences row", () => {
    mockUseRepoPreferences.mockReturnValue({ data: null, isLoading: false });
    renderSection();
    expect(screen.getByLabelText("Base branch")).toHaveValue("");
  });
});

describe("RepoPreferencesSection heading", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({ data: PREFS, isLoading: false });
  });

  it("heads the section in sentence case, like every other section title", () => {
    renderSection();
    expect(screen.getByRole("heading", { name: "Sandbox preferences" })).toBeInTheDocument();
  });
});

describe("RepoPreferencesSection save", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({ data: PREFS, isLoading: false });
  });

  // The section sits on the repo page under the QA config, and on the
  // Environments Repository tab beside Add variable. Either page has one
  // gold action already, so this Save is the secondary button.
  it("saves with the secondary button, never the gold primary", () => {
    renderSection();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save.className).toContain("bg-overlay");
    expect(save.className).not.toMatch(/(^|\s)bg-primary(\s|$)/);
  });
});
