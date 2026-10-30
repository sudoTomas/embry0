import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockMutate = vi.fn();
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock("@/hooks/useIssues", () => ({
  useCreateIssue: () => ({ mutate: mockMutate, isPending: false }),
}));
// The forge token sees a repository embry0 has not registered; the dialog must not offer it.
vi.mock("@/hooks/useGitHub", () => ({
  useGitHubRepos: () => ({ data: { repos: [{ full_name: "acme/unlisted" }] }, isLoading: false, isError: false }),
}));
const mockRegistered = vi.fn();
vi.mock("@/hooks/useRepos", () => ({ useRegisteredRepos: () => mockRegistered() }));
vi.mock("sonner", () => ({
  toast: { success: (m: string) => mockToastSuccess(m), error: (m: string) => mockToastError(m) },
}));

import { CreateIssueDialog } from "../CreateIssueDialog";

async function submitWith(response: {
  job_id: string | null;
  dispatch_error_code: string | null;
  dispatch_error: string | null;
}) {
  mockMutate.mockImplementation((_req, opts) => opts.onSuccess(response));
  const onClose = vi.fn();
  render(<CreateIssueDialog onClose={onClose} />);
  await userEvent.type(screen.getByLabelText(/title/i), "Fix the thing");
  await userEvent.click(screen.getByRole("button", { name: /create/i }));
  return onClose;
}

const REGISTERED = [{ repo: "acme/widgets" }, { repo: "acme/gadgets" }];
const FIRST_TASK_HINT = "Pick something small and low-risk. embry0 opens a pull request for you to review. It never merges.";

beforeEach(() => {
  vi.clearAllMocks();
  mockRegistered.mockReturnValue({ data: REGISTERED, isLoading: false, isError: false });
});

/** The repository names the picker offers once the person focuses it. */
async function offeredRepos(): Promise<string[]> {
  const input = screen.getByPlaceholderText("Select repository...");
  await userEvent.clear(input);
  return within(input.parentElement as HTMLElement)
    .queryAllByRole("button")
    .map((b) => b.textContent ?? "");
}

describe("CreateIssueDialog", () => {
  it("confirms a created issue whose job started", async () => {
    const onClose = await submitWith({ job_id: "job-1", dispatch_error_code: null, dispatch_error: null });
    expect(mockToastSuccess).toHaveBeenCalledWith("Issue created");
    expect(mockToastError).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("says so when auto-triage could not start a job", async () => {
    const onClose = await submitWith({
      job_id: null,
      dispatch_error_code: "ERR_DISPATCH_FAILED",
      dispatch_error: "The issue was saved but no job was started (boom-free message)",
    });
    expect(mockToastError).toHaveBeenCalledWith(expect.stringContaining("no job was started"));
    expect(mockToastError).toHaveBeenCalledWith(expect.stringContaining("boom-free message"));
    expect(mockToastSuccess).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("offers only repositories registered in embry0", async () => {
    render(<CreateIssueDialog onClose={vi.fn()} />);
    expect(await offeredRepos()).toEqual(["acme/gadgets", "acme/widgets"]);
    expect(screen.getByText("Only repositories registered in embry0 are listed.")).toBeInTheDocument();
    expect(screen.queryByText(FIRST_TASK_HINT)).not.toBeInTheDocument();
  });

  it("says so when the registered repositories cannot load", () => {
    mockRegistered.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<CreateIssueDialog onClose={vi.fn()} />);
    expect(screen.getByText("Could not load the registered repositories.")).toBeInTheDocument();
  });

  it("opens on the repository it was given, with a hint for a first task, and creates it there", async () => {
    mockMutate.mockImplementation((_req, opts) => opts.onSuccess({ job_id: "job-1", dispatch_error_code: null, dispatch_error: null }));
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(<CreateIssueDialog onClose={onClose} onCreated={onCreated} initialRepo="acme/widgets" />);
    expect(screen.getByPlaceholderText("Select repository...")).toHaveValue("acme/widgets");
    const title = screen.getByLabelText(/title/i);
    const hint = screen.getByText(FIRST_TASK_HINT);
    expect(title.compareDocumentPosition(hint) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await userEvent.type(title, "Fix the footer link");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Fix the footer link", repo: "acme/widgets" }),
      expect.anything(),
    );
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
  });

  // The picker closes its list 150 ms after it loses focus, and a click on
  // an option lands inside that window. The pick must outlast it: the close
  // used to put the filter typed to find the option back, and the dialog
  // then created the issue on a repository named "wid" (#267).
  it("creates the issue on the repository picked from the list, not on the filter typed to find it", async () => {
    mockMutate.mockImplementation((_req, opts) => opts.onSuccess({ job_id: "job-1", dispatch_error_code: null, dispatch_error: null }));
    render(<CreateIssueDialog onClose={vi.fn()} />);
    const picker = screen.getByPlaceholderText("Select repository...");
    await userEvent.type(picker, "wid");
    await userEvent.click(screen.getByRole("button", { name: "acme/widgets" }));
    expect(picker).toHaveValue("acme/widgets");

    await new Promise((resolve) => setTimeout(resolve, 200));
    // Soft, so a regression also reports what the dialog then submits.
    expect.soft(picker, "the pick outlasts the list's 150 ms close").toHaveValue("acme/widgets");

    await userEvent.type(screen.getByLabelText(/title/i), "Fix the footer link");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(mockMutate).toHaveBeenCalledWith(expect.objectContaining({ repo: "acme/widgets" }), expect.anything());
  });

  it("still offers a hidden repository it was opened for", async () => {
    render(<CreateIssueDialog onClose={vi.fn()} initialRepo="acme/hidden" />);
    expect(screen.getByPlaceholderText("Select repository...")).toHaveValue("acme/hidden");
    expect(await offeredRepos()).toEqual(["acme/gadgets", "acme/hidden", "acme/widgets"]);
  });

  // The Issues page renders it inline in a space-y stack, whose bottom
  // margin cut 24 px off the overlay while it lived there.
  it("renders its overlay as a child of body, outside the page that opens it", () => {
    const { container } = render(
      <div className="space-y-6">
        <CreateIssueDialog onClose={vi.fn()} />
        <p>The rest of the page</p>
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: "Create issue dialog" });
    expect(container).not.toContainElement(dialog);
    expect(dialog.parentElement).toBe(document.body);
  });

  it("does not report a creation that failed", async () => {
    mockMutate.mockImplementation((_req, opts) => opts.onError(new Error("boom")));
    const onCreated = vi.fn();
    render(<CreateIssueDialog onClose={vi.fn()} onCreated={onCreated} initialRepo="acme/widgets" />);
    await userEvent.type(screen.getByLabelText(/title/i), "Fix the footer link");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(onCreated).not.toHaveBeenCalled();
  });
});
