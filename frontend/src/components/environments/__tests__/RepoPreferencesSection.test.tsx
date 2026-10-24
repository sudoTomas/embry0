import { describe, it, expect, vi, beforeEach } from "vitest";
import { useLayoutEffect } from "react";
import { createRoot } from "react-dom/client";
import { render, screen, waitFor, within } from "@testing-library/react";
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

const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() } }));

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

  it("leaves its heading out when the repo page's Advanced section already names it", () => {
    const { unmount } = renderSection();
    expect(screen.getByRole("heading", { name: "Work machine and branches" })).toBeInTheDocument();
    unmount();
    render(
      <MemoryRouter>
        <RepoPreferencesSection repo="acme/group/backend" showIntro={false} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("heading", { name: "Work machine and branches" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("heading", { name: "Work machine and branches" })).toBeInTheDocument();
  });

  it("says what the section holds, in plain words", () => {
    renderSection();
    expect(
      screen.getByText(
        "The work machine, the branch embry0 starts from and asks to merge into, the commit author, and the checks it runs before a pull request.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/triage/)).not.toBeInTheDocument();
  });

  it("names the blank work machine embry0's default", () => {
    renderSection();
    const picker = screen.getByLabelText("Work machine");
    expect(within(picker).getByRole("option", { name: "embry0's default" })).toHaveValue("");
    expect(within(picker).queryByRole("option", { name: /triage/ })).toBeNull();
  });

  it("no longer links to the old QA config page", () => {
    renderSection();
    expect(screen.queryByTestId("repo-prefs-qa-config-link")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("RepoPreferencesSection clear", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({ data: PREFS, isLoading: false });
  });

  const CONFIRM =
    "Clear the work machine, branch, commit author, checks before a pull request and notes for acme/group/backend? This also turns off pull request review. You can undo this from History.";

  it("asks first, and a cancel sends no DELETE", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderSection();
    await user.click(screen.getByRole("button", { name: "Clear these settings" }));
    expect(confirmSpy).toHaveBeenCalledWith(CONFIRM);
    expect(mockDeleteMutate).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Base branch")).toHaveValue("dev");
    confirmSpy.mockRestore();
  });

  it("clears after the confirm and says it can be undone", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockDeleteMutate.mockImplementation((_vars, opts: { onSuccess: () => void }) => opts.onSuccess());
    renderSection();
    await user.click(screen.getByRole("button", { name: "Clear these settings" }));
    expect(mockDeleteMutate).toHaveBeenCalledWith({ fullRepo: "acme/group/backend" }, expect.anything());
    expect(toastSuccess).toHaveBeenCalledWith("Settings cleared. You can undo this from History.");
    expect(screen.getByLabelText("Base branch")).toHaveValue("");
    confirmSpy.mockRestore();
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

describe("RepoPreferencesSection first frame", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({
      data: { ...PREFS, dev_checks: [{ name: "python", paths: ["ml"], command: "pytest -q" }] },
      isLoading: false,
    });
  });

  it("draws the stored dev checks in the first frame a browser can paint", async () => {
    // Outside act, which runs every effect before a test can look: what the
    // page holds when the task that drew it ends is what a browser paints.
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const actEnvironment = env.IS_REACT_ACT_ENVIRONMENT;
    env.IS_REACT_ACT_ENVIRONMENT = false;
    const painted: number[] = [];
    function FrameEnd() {
      useLayoutEffect(() => {
        queueMicrotask(() => painted.push(document.querySelectorAll('[data-testid="dev-check-row"]').length));
      }, []);
      return null;
    }
    const box = document.body.appendChild(document.createElement("div"));
    const root = createRoot(box);
    try {
      root.render(
        <MemoryRouter>
          <RepoPreferencesSection repo="acme/group/backend" />
          <FrameEnd />
        </MemoryRouter>,
      );
      await new Promise((r) => setTimeout(r, 50));
    } finally {
      root.unmount();
      box.remove();
      env.IS_REACT_ACT_ENVIRONMENT = actEnvironment;
    }
    expect(painted).toEqual([1]);
  });
});

describe("RepoPreferencesSection owners (owner proposals, spec 2026-10-24)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseRepoPreferences.mockReturnValue({ data: { ...PREFS, owners: ["octo", "pat"] }, isLoading: false });
  });

  it("shows the stored owners and saves edited ones as logins", async () => {
    const user = userEvent.setup();
    renderSection();
    const input = screen.getByLabelText("Owners");
    expect(input).toHaveValue("octo, pat");
    await user.clear(input);
    await user.type(input, "@octo,  lee-1 ");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update.owners).toEqual(["octo", "lee-1"]);
  });

  it("sends null for no owners, so CODEOWNERS answers", async () => {
    const user = userEvent.setup();
    renderSection();
    await user.clear(screen.getByLabelText("Owners"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update.owners).toBeNull();
  });
});
