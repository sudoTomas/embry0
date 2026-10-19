import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const mockFetchProposals = vi.fn();
const mockAcceptCase = vi.fn();
const mockRejectCase = vi.fn();
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock("@/api/cases", () => ({
  fetchProposals: () => mockFetchProposals(),
  acceptCase: (id: string) => mockAcceptCase(id),
  rejectCase: (id: string) => mockRejectCase(id),
}));
vi.mock("sonner", () => ({
  toast: { success: (m: string) => mockToastSuccess(m), error: (m: string) => mockToastError(m) },
}));

import { ProposalsSection } from "../ProposalsSection";

const HOUR = 3_600_000;

const LINEAR_DRAFT = {
  id: "hc-linear",
  repo: "acme/backend",
  title: "Fix the retry storm",
  origin: "watcher",
  stage: "tracked",
  severity: "medium",
  tracker: { kind: "linear", key: "ENG-1", url: "https://linear.app/acme/issue/ENG-1" },
  // Detected five days ago, filed two days ago: it waited for a draft slot.
  timestamps: {
    detected_at: new Date(Date.now() - 120 * HOUR).toISOString(),
    tracked_at: new Date(Date.now() - 49 * HOUR).toISOString(),
  },
};

const GITHUB_DRAFT = {
  ...LINEAR_DRAFT,
  id: "hc-github",
  repo: "acme/widgets",
  title: "Cleanup fails when the daemon is down",
  tracker: { kind: "github", key: "414", url: "https://github.com/acme/widgets/issues/414" },
};

const GITLAB_DRAFT = {
  ...LINEAR_DRAFT,
  id: "hc-gitlab",
  repo: "acme/gateway",
  title: "Queue consumer restarts in a loop",
  tracker: { kind: "gitlab", key: "7", url: "https://gitlab.example.com/acme/gateway/-/issues/7" },
};

// ADR 0011: an organization's need, filed on the owner because it covers several repositories.
const ORG_NEED = {
  id: "hc-org",
  repo: "acme",
  title: "Register 3 repositories",
  origin: "org",
  stage: "detected",
  severity: null,
  tracker: null,
  timestamps: { detected_at: new Date(Date.now() - 3 * HOUR).toISOString() },
};

// The same kind of need on one repository: filed on that repository.
const ORG_NEED_ONE_REPO = {
  ...ORG_NEED,
  id: "hc-org-one",
  repo: "acme/api",
  title: "Add the webhook to 1 repository",
};

function page(cases: unknown[]) {
  return { cases, total: cases.length, limit: 50, next_cursor: null };
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ProposalsSection />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

async function row(title: string) {
  return (await screen.findByText(title)).closest("tr")!;
}

let confirmSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  // Dismissing an organization need is admin work; these tests act as the API key.
  useAuthStore.setState({ status: "authenticated", authMode: "key", user: null });
  mockFetchProposals.mockResolvedValue(page([LINEAR_DRAFT, GITHUB_DRAFT]));
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => confirmSpy.mockRestore());

describe("ProposalsSection", () => {
  it("renders nothing when no draft is waiting", async () => {
    mockFetchProposals.mockResolvedValue(page([]));
    const { container } = renderSection();
    await waitFor(() => expect(mockFetchProposals).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each draft with its ticket, repo and how long it has waited", async () => {
    renderSection();
    const linear = await row("Fix the retry storm");
    expect(within(linear).getByRole("link", { name: "ENG-1" })).toHaveAttribute(
      "href",
      "https://linear.app/acme/issue/ENG-1",
    );
    expect(within(linear).getByText("acme/backend")).toBeInTheDocument();
    // Counted from when the draft was filed, not when the fault was first seen.
    expect(within(linear).getByText("2 days")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Proposals" })).toBeInTheDocument();
  });

  it("heads the ticket column Source, since an organization need has no ticket", async () => {
    mockFetchProposals.mockResolvedValue(page([LINEAR_DRAFT, ORG_NEED]));
    renderSection();
    await row("Register 3 repositories");
    const heads = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(heads).toContain("Source");
    expect(heads).not.toContain("Ticket");
  });

  it("offers Accept for GitHub and Linear drafts, and points at the tracker for GitLab", async () => {
    mockFetchProposals.mockResolvedValue(page([LINEAR_DRAFT, GITHUB_DRAFT, GITLAB_DRAFT]));
    renderSection();
    const github = await row("Cleanup fails when the daemon is down");
    expect(within(github).getByRole("button", { name: "Accept" })).toBeInTheDocument();

    // #460: a Linear draft is accepted here; nobody is sent to Linear to label it.
    const linear = await row("Fix the retry storm");
    expect(within(linear).getByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(within(linear).queryByText(/accept by label/i)).not.toBeInTheDocument();

    const gitlab = await row("Queue consumer restarts in a loop");
    expect(within(gitlab).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(within(gitlab).getByRole("link", { name: /accept by label in gitlab/i })).toHaveAttribute(
      "href",
      "https://gitlab.example.com/acme/gateway/-/issues/7",
    );
    // Rejecting is the same decision in every tracker.
    for (const tr of [linear, github, gitlab]) {
      expect(within(tr).getByRole("button", { name: "Reject" })).toBeInTheDocument();
    }
  });

  it("accepts a Linear draft through the API, saying first that the ticket gets the trigger label", async () => {
    mockAcceptCase.mockResolvedValue({ case_id: "hc-linear", issue_id: "iss-2", job_id: "job-0123456789ab" });
    renderSection();
    await userEvent.click(within(await row("Fix the retry storm")).getByRole("button", { name: "Accept" }));
    expect(confirmSpy.mock.calls[0][0]).toBe(
      'Accept "Fix the retry storm"? This adds the trigger label to ENG-1 in Linear and starts the fix pipeline on acme/backend.',
    );
    await waitFor(() => expect(mockAcceptCase).toHaveBeenCalledWith("hc-linear"));
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith("Accepted — job job-01234567 started"));
  });

  it("does not claim to label a GitHub draft, which is dispatched as it is", async () => {
    mockAcceptCase.mockResolvedValue({ case_id: "hc-github", issue_id: "iss-1", job_id: "job-abc123def456" });
    renderSection();
    await userEvent.click(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Accept" }));
    expect(confirmSpy.mock.calls[0][0]).toBe(
      'Accept "Cleanup fails when the daemon is down"? This starts the fix pipeline on acme/widgets.',
    );
  });

  it("accepts a draft after confirmation and reports the job it started", async () => {
    mockAcceptCase.mockResolvedValue({ case_id: "hc-github", issue_id: "iss-1", job_id: "job-abc123def456" });
    renderSection();
    await userEvent.click(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(mockAcceptCase).toHaveBeenCalledWith("hc-github"));
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith("Accepted — job job-abc123de started"));
  });

  it("does nothing when the confirmation is declined", async () => {
    confirmSpy.mockReturnValue(false);
    renderSection();
    await userEvent.click(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Accept" }));
    await userEvent.click(within(await row("Fix the retry storm")).getByRole("button", { name: "Reject" }));
    expect(mockAcceptCase).not.toHaveBeenCalled();
    expect(mockRejectCase).not.toHaveBeenCalled();
  });

  it("rejects a draft and says the ticket is still open in its tracker", async () => {
    mockRejectCase.mockResolvedValue(undefined);
    renderSection();
    await userEvent.click(within(await row("Fix the retry storm")).getByRole("button", { name: "Reject" }));
    expect(confirmSpy.mock.calls[0][0]).toContain("ENG-1 stays open in Linear");
    await waitFor(() => expect(mockRejectCase).toHaveBeenCalledWith("hc-linear"));
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith("Rejected. ENG-1 is still open in Linear."));
  });

  it("shows the server's reason when a decision is refused", async () => {
    mockAcceptCase.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: "acme/widgets#414 is closed on GitHub" } },
    });
    renderSection();
    await userEvent.click(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledWith("acme/widgets#414 is closed on GitHub"));
  });

  it("shows a ticket with no stored url as plain text", async () => {
    mockFetchProposals.mockResolvedValue(
      page([
        { ...LINEAR_DRAFT, tracker: { kind: "linear", key: "ENG-9", url: null } },
        { ...GITLAB_DRAFT, tracker: { kind: "gitlab", key: "8", url: null } },
      ]),
    );
    renderSection();
    const linear = await row("Fix the retry storm");
    expect(within(linear).getByText("ENG-9")).toBeInTheDocument();
    expect(within(linear).queryByRole("link")).not.toBeInTheDocument();
    // Accepting needs no url: the API addresses the ticket by its key.
    expect(within(linear).getByRole("button", { name: "Accept" })).toBeInTheDocument();

    const gitlab = await row("Queue consumer restarts in a loop");
    expect(within(gitlab).queryByRole("link")).not.toBeInTheDocument();
    expect(within(gitlab).getByText("Accept by label in GitLab")).toBeInTheDocument();
  });

  it("shows an organization need with no ticket, sends the person to its organization, and offers Dismiss", async () => {
    mockFetchProposals.mockResolvedValue(page([ORG_NEED, GITHUB_DRAFT]));
    renderSection();
    const need = await row("Register 3 repositories");
    expect(within(need).getByText("Organization need")).toBeInTheDocument();
    expect(within(need).getByRole("link", { name: "Answer on acme" })).toHaveAttribute("href", "/orgs/acme");
    expect(within(need).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(within(need).queryByText(/accept by label/i)).not.toBeInTheDocument();
    expect(within(need).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    // A watcher draft beside it is unchanged.
    expect(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("offers Dismiss on an organization need only to an admin", async () => {
    // Dismissing a need is admin-only on the organization route, and closing
    // its case is the same answer, so an operator is not offered it here.
    useAuthStore.setState({ authMode: "session", user: { email: "o@x", name: "O", tier: "operator" } });
    mockFetchProposals.mockResolvedValue(page([ORG_NEED, GITHUB_DRAFT]));
    renderSection();
    const need = await row("Register 3 repositories");
    expect(within(need).queryByRole("button", { name: "Dismiss" })).toBeNull();
    expect(within(need).getByRole("link", { name: "Answer on acme" })).toBeInTheDocument();
    // An operator still rejects a watcher draft.
    expect(within(await row("Cleanup fails when the daemon is down")).getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("links a need filed on a bare owner to its organization, never to a repository", async () => {
    mockFetchProposals.mockResolvedValue(page([ORG_NEED, ORG_NEED_ONE_REPO]));
    renderSection();
    const owner = await row("Register 3 repositories");
    const hrefs = within(owner)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/orgs/acme", "/orgs/acme"]);
    expect(within(owner).getByRole("link", { name: "acme" })).toHaveAttribute("href", "/orgs/acme");

    const one = await row("Add the webhook to 1 repository");
    expect(within(one).getByText("acme/api").closest("a")).toBeNull();
    expect(within(one).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/orgs/acme"]);
  });

  it("dismisses an organization need without talking about a ticket", async () => {
    mockFetchProposals.mockResolvedValue(page([ORG_NEED]));
    mockRejectCase.mockResolvedValue(undefined);
    renderSection();
    await userEvent.click(within(await row("Register 3 repositories")).getByRole("button", { name: "Dismiss" }));
    expect(confirmSpy.mock.calls[0][0]).toBe(
      'Dismiss "Register 3 repositories"? It stays dismissed on the acme needs list until someone reopens it.',
    );
    await waitFor(() => expect(mockRejectCase).toHaveBeenCalledWith("hc-org"));
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalledWith("Dismissed. Reopen it from the acme needs list."));
  });

  it("says so when the list cannot be loaded, instead of looking empty", async () => {
    mockFetchProposals.mockRejectedValue(new Error("boom"));
    renderSection();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the watcher's proposals.");
  });
});
