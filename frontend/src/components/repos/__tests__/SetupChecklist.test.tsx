import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Readiness, ReadinessRow, SetupProposal } from "@/api/repoSetup";

const mockFetchReadiness = vi.fn();
const mockSkipTesting = vi.fn();
const mockFetchSetupCost = vi.fn();
const mockStartSetup = vi.fn();
const mockFetchProposal = vi.fn();
vi.mock("@/api/repoSetup", () => ({
  fetchReadiness: (...a: unknown[]) => mockFetchReadiness(...a),
  skipTesting: (...a: unknown[]) => mockSkipTesting(...a),
  fetchSetupCost: (...a: unknown[]) => mockFetchSetupCost(...a),
  startSetup: (...a: unknown[]) => mockStartSetup(...a),
  fetchProposal: (...a: unknown[]) => mockFetchProposal(...a),
  applyProposal: vi.fn(),
  rejectProposal: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SetupChecklist } from "../SetupChecklist";
import { readinessRefetchInterval } from "@/hooks/useRepoReadiness";

const TITLES: Record<ReadinessRow["key"], string> = {
  code_access: "Read your code and open pull requests",
  app_values: "Settings your app needs",
  testing: "Test your app before showing you a change",
  first_task: "Finish a first task",
  work_trigger: "Start work from an issue",
};

function row(
  key: ReadinessRow["key"],
  state: ReadinessRow["state"],
  says: string,
  extra: Partial<ReadinessRow> = {},
): ReadinessRow {
  return { key, required: key !== "work_trigger", state, title: TITLES[key], says, action: null, ...extra };
}

const NOT_SET_UP = "Testing is not set up yet. embry0 can still write code, but it cannot check that your app still works.";
const START = { kind: "start_setup", label: "Let embry0 set up testing" } as const;
const COPY = { kind: "copy_admin_request", label: "Copy a request for your admin" } as const;

const CODE_DONE = row("code_access", "done", "Uses the GitHub account acme-bot.");
const VALUES_DONE = row("app_values", "done", "Every setting your app lists has a value.");
const FIRST_WAITING = row("first_task", "waiting", "Comes after testing is set up or skipped.");
const TRIGGER_UNKNOWN = row(
  "work_trigger",
  "unknown",
  "Put the label embry0 on an issue here to start work. embry0 cannot tell whether GitHub tells it about new issues in this repository.",
  { trigger_label: "embry0" },
);

function readiness(overrides: Partial<Readiness>): Readiness {
  return {
    repo: "acme/widgets",
    status: "steps_left",
    steps_left: 2,
    summary: "embry0 can read this code and open pull requests. It cannot test your app yet.",
    next_step: "testing",
    rows: [],
    needs_docker: false,
    facts_seen_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

const UNCONFIGURED = readiness({
  rows: [CODE_DONE, VALUES_DONE, row("testing", "missing", NOT_SET_UP, { action: START }), FIRST_WAITING, TRIGGER_UNKNOWN],
});

const IN_PROGRESS = readiness({
  next_step: null,
  rows: [
    CODE_DONE,
    VALUES_DONE,
    row("testing", "in_progress", "embry0 is reading your code and trying to start your app. You can leave this page.", {
      action: { kind: "view_job", label: "See progress" },
      job_id: "job-123",
      proposal_id: "prop-abc",
    }),
    FIRST_WAITING,
    TRIGGER_UNKNOWN,
  ],
});

const REVIEW = readiness({
  status: "setup_to_review",
  rows: [
    CODE_DONE,
    VALUES_DONE,
    row("testing", "review", "embry0 has a test setup for you to review.", {
      action: { kind: "review_proposal", label: "Review the setup" },
      job_id: "job-123",
      proposal_id: "prop-abc",
    }),
    FIRST_WAITING,
    TRIGGER_UNKNOWN,
  ],
});

const CONFIGURED = readiness({
  status: "ready",
  steps_left: 0,
  next_step: null,
  summary: "embry0 can read this code, open pull requests and test your app.",
  rows: [
    CODE_DONE,
    VALUES_DONE,
    row("testing", "done", "Tests web and checks 3 things after each change."),
    row("first_task", "done", "First pull request opened.", { pr_url: "https://github.com/acme/widgets/pull/7" }),
    row(
      "work_trigger",
      "done",
      "Put the label embry0 on an issue here to start work. GitHub tells embry0 about new issues in this repository.",
      { trigger_label: "embry0" },
    ),
  ],
});

const NEEDS_ADMIN = readiness({
  status: "needs_admin",
  steps_left: 4,
  next_step: "code_access",
  rows: [
    row("code_access", "needs_admin", "An admin has to connect a GitHub account that can see this repository.", {
      action: COPY,
    }),
    row("app_values", "missing", "Your app lists 3 settings. 2 have no value yet: API_URL, API_TOKEN.", {
      action: { kind: "open_environment", label: "Fill in 2 values" },
      missing_keys: ["API_URL", "API_TOKEN"],
    }),
    row("testing", "missing", NOT_SET_UP, { action: START }),
    FIRST_WAITING,
    row(
      "work_trigger",
      "missing",
      "GitHub does not tell embry0 about new issues here yet, so the label embry0 does nothing. An admin can add the connection from Organizations.",
      { action: COPY, trigger_label: "embry0" },
    ),
  ],
});

const FAILED = readiness({
  rows: [
    CODE_DONE,
    VALUES_DONE,
    row("testing", "failed", "embry0 could not set up testing: the app did not start", {
      action: { kind: "start_setup", label: "Try again" },
      proposal_id: "prop-abc",
    }),
    FIRST_WAITING,
    TRIGGER_UNKNOWN,
  ],
});

const PROPOSAL: SetupProposal = {
  id: "prop-abc",
  repo: "acme/widgets",
  status: "pending",
  job_id: "job-123",
  job_status: "completed",
  created_by: "alex@example.com",
  created_at: "2026-10-18T10:00:00Z",
  decided_by: null,
  decided_at: null,
  notes_md: "",
  failure: "",
  smoke: { results: { web: "passed" }, failures: [] },
  proposed_yaml: "version: 2\n",
  current_yaml: null,
  check: {
    ok: true,
    problems: [],
    warnings: [],
    summary: {
      when: "auto",
      when_plain: "When a change touches the app",
      apps: [
        {
          name: "web",
          target: "managed",
          start: "npm run dev",
          address: "http://localhost:5173",
          address_is_local: true,
          criteria: ["The home page loads"],
          guardrails: 0,
          machine: "qa-external",
          machine_has_browser: true,
        },
      ],
    },
  },
};

const GOLD = /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/;

/** Every element drawn in the gold: a gold fill, a gold wash or gold text. */
function goldElements(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));
}

function renderChecklist(repo = "acme/widgets", forge: "github" | "gitlab" = "github") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SetupChecklist repo={repo} forge={forge} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function renderWith(data: Readiness) {
  mockFetchReadiness.mockResolvedValue(data);
  renderChecklist();
  await screen.findByText(data.summary);
  return screen.getByTestId("setup-checklist");
}

const rowEl = (key: string) => screen.getByTestId(`setup-row-${key}`);

beforeEach(() => {
  mockFetchReadiness.mockReset();
  mockSkipTesting.mockReset();
  mockFetchSetupCost.mockReset();
  mockStartSetup.mockReset();
  mockFetchProposal.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("SetupChecklist", () => {
  it("opens on the card title, the summary and the reassurance line", async () => {
    const card = await renderWith(UNCONFIGURED);
    expect(within(card).getByRole("heading", { name: "Getting this repository ready" })).toBeInTheDocument();
    expect(card).toHaveTextContent(
      "embry0 works on its own branch and opens a pull request for you to review. It never merges your code.",
    );
    expect(mockFetchReadiness).toHaveBeenCalledWith("acme/widgets");
    for (const title of Object.values(TITLES)) expect(within(card).getByText(title)).toBeInTheDocument();
  });

  it("gives each row its state and its plain sentence", async () => {
    await renderWith(NEEDS_ADMIN);
    expect(rowEl("code_access")).toHaveAttribute("data-state", "needs_admin");
    expect(rowEl("code_access")).toHaveTextContent("Needs an admin");
    expect(rowEl("app_values")).toHaveTextContent("2 have no value yet: API_URL, API_TOKEN.");
    expect(rowEl("first_task")).toHaveAttribute("data-state", "waiting");
    expect(rowEl("work_trigger")).toHaveTextContent("Optional");
    expect(rowEl("testing")).not.toHaveTextContent("Optional");
  });

  it.each([
    ["unconfigured", UNCONFIGURED, "Let embry0 set up testing"],
    ["review", REVIEW, "Review the setup"],
  ])("spends its one gold on the next step (%s)", async (_name, data, label) => {
    const card = await renderWith(data);
    const gold = goldElements(card);
    expect(gold).toHaveLength(1);
    expect(gold[0]).toBe(within(rowEl("testing")).getByRole("button", { name: label }));
  });

  it.each([
    ["in progress", IN_PROGRESS],
    ["configured", CONFIGURED],
  ])("draws no gold when there is no next step (%s)", async (_name, data) => {
    const card = await renderWith(data);
    expect(goldElements(card)).toEqual([]);
  });

  it("gives a step only an admin can do a copyable request, drawn quietly", async () => {
    const user = userEvent.setup();
    const card = await renderWith(NEEDS_ADMIN);
    const copy = within(rowEl("code_access")).getByRole("button", { name: "Copy a request for your admin" });
    expect(copy.className).toContain("border-border-strong");
    expect(goldElements(card)).toEqual([]);

    await user.click(copy);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Copied. Send it to your embry0 admin."));
    expect(await navigator.clipboard.readText()).toBe(
      "Please connect a GitHub account that can read and push to acme/widgets in embry0. Open Credentials, add the account, " +
        `then pick it on ${window.location.origin}/repos/acme/widgets under Advanced settings, Accounts.`,
    );
  });

  it("asks an admin for the issue connection on the work-trigger row", async () => {
    const user = userEvent.setup();
    await renderWith(NEEDS_ADMIN);
    await user.click(within(rowEl("work_trigger")).getByRole("button", { name: "Copy a request for your admin" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(await navigator.clipboard.readText()).toBe(
      "Please add the embry0 connection for acme/widgets: open Organizations, choose acme, and use Add webhook.",
    );
  });

  it("names GitLab in the request for a GitLab repository", async () => {
    const user = userEvent.setup();
    mockFetchReadiness.mockResolvedValue({ ...NEEDS_ADMIN, repo: "group/sub/project" });
    renderChecklist("group/sub/project", "gitlab");
    await screen.findByText(NEEDS_ADMIN.summary);
    await user.click(within(rowEl("code_access")).getByRole("button", { name: "Copy a request for your admin" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(await navigator.clipboard.readText()).toContain(
      "Please connect a GitLab account that can read and push to group/sub/project in embry0.",
    );
  });

  it("shows the request itself when the browser will not copy", async () => {
    const user = userEvent.setup();
    await renderWith(NEEDS_ADMIN);
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("blocked"));
    await user.click(within(rowEl("code_access")).getByRole("button", { name: "Copy a request for your admin" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not copy the request. Send this to your embry0 admin:", {
        description: expect.stringContaining("Please connect a GitHub account"),
      }),
    );
  });

  it("renders unknown rows with their sentence and no gold", async () => {
    await renderWith(
      readiness({
        rows: [
          CODE_DONE,
          row("app_values", "unknown", "embry0 has not read this repository's example settings file yet.", {
            action: { kind: "open_environment", label: "Check for settings" },
          }),
          row("testing", "missing", NOT_SET_UP, { action: START }),
          FIRST_WAITING,
          TRIGGER_UNKNOWN,
        ],
      }),
    );
    for (const key of ["app_values", "work_trigger"]) {
      expect(rowEl(key)).toHaveAttribute("data-state", "unknown");
      expect(goldElements(rowEl(key))).toEqual([]);
    }
    expect(rowEl("app_values")).toHaveTextContent("embry0 has not read this repository's example settings file yet.");
    expect(rowEl("work_trigger")).toHaveTextContent("embry0 cannot tell whether GitHub tells it about new issues");
  });

  it("links each action to the place that answers it", async () => {
    await renderWith(NEEDS_ADMIN);
    expect(within(rowEl("app_values")).getByRole("link", { name: "Fill in 2 values" })).toHaveAttribute(
      "href",
      "/environments?repo=acme%2Fwidgets",
    );
  });

  it("asks before embry0 sets up testing, and starts nothing until Start", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockReturnValue(new Promise(() => {}));
    await renderWith(UNCONFIGURED);
    expect(within(rowEl("testing")).queryByRole("link")).not.toBeInTheDocument();
    await user.click(within(rowEl("testing")).getByRole("button", { name: "Let embry0 set up testing" }));
    const dialog = screen.getByRole("dialog", { name: "Let embry0 set up testing" });
    expect(screen.getByTestId("setup-checklist")).not.toContainElement(dialog);
    expect(mockFetchSetupCost).toHaveBeenCalledWith("acme/widgets");
    expect(mockStartSetup).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockStartSetup).not.toHaveBeenCalled();
  });

  it("closes the dialog once embry0 has started", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockResolvedValue({
      recorded_runs: 0,
      typical_cost_usd: null,
      per_job_budget_usd: 10,
      model: "medium-model",
      paid_from: null,
    });
    mockStartSetup.mockResolvedValue({ proposal_id: "prop-abc", job_id: "job-123" });
    await renderWith(UNCONFIGURED);
    await user.click(within(rowEl("testing")).getByRole("button", { name: "Let embry0 set up testing" }));
    const dialog = screen.getByRole("dialog", { name: "Let embry0 set up testing" });
    await within(dialog).findByText("No setup run is recorded here yet.");
    await user.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockStartSetup).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mockFetchReadiness).toHaveBeenCalledTimes(2));
  });

  it("opens the review card under the testing row and keeps one gold", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValue(PROPOSAL);
    const card = await renderWith(REVIEW);
    const toggle = within(rowEl("testing")).getByRole("button", { name: "Review the setup" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("setup-proposal")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const proposal = within(rowEl("testing")).getByTestId("setup-proposal");
    const apply = await within(proposal).findByRole("button", { name: "Apply this setup" });
    expect(goldElements(card)).toEqual([apply]);
    expect(toggle.className).toContain("border-border-strong");

    await user.click(within(proposal).getByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("setup-proposal")).not.toBeInTheDocument();
    expect(goldElements(card)).toEqual([toggle]);
  });

  it("keeps the next step's gold on the row while a review card stays closed", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValue(PROPOSAL);
    const card = await renderWith(REVIEW);
    const toggle = within(rowEl("testing")).getByRole("button", { name: "Review the setup" });
    await user.click(toggle);
    await user.click(toggle);
    expect(screen.queryByTestId("setup-proposal")).not.toBeInTheDocument();
    expect(goldElements(card)).toEqual([toggle]);
  });

  it("shows what happened in a failed run, and tries again from there", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValue({
      ...PROPOSAL,
      status: "failed",
      failure: "the app did not start",
      proposed_yaml: null,
      check: null,
      smoke: null,
    });
    mockFetchSetupCost.mockReturnValue(new Promise(() => {}));
    const card = await renderWith(FAILED);
    const testing = rowEl("testing");
    expect(goldElements(card)).toEqual([within(testing).getByRole("button", { name: "Try again" })]);

    await user.click(within(testing).getByRole("button", { name: "See what happened" }));
    const proposal = within(testing).getByTestId("setup-proposal");
    await within(proposal).findByText(/embry0 could not set up testing: the app did not start/);
    const tryAgain = within(proposal).getByRole("button", { name: "Try again" });
    expect(within(testing).getAllByRole("button", { name: "Try again" })).toEqual([tryAgain]);
    expect(within(testing).getAllByRole("button", { name: "Skip testing for now" })).toHaveLength(1);
    expect(goldElements(card)).toEqual([tryAgain]);

    await user.click(tryAgain);
    expect(screen.getByRole("dialog", { name: "Let embry0 set up testing" })).toBeInTheDocument();
  });

  it("links progress, a fix, a first task and the first pull request", async () => {
    mockFetchReadiness.mockResolvedValue(IN_PROGRESS);
    const { unmount } = renderChecklist();
    expect(await screen.findByRole("link", { name: "See progress" })).toHaveAttribute("href", "/jobs/job-123");
    unmount();

    await renderWith(
      readiness({
        rows: [
          CODE_DONE,
          VALUES_DONE,
          row("testing", "needs_fix", "The work machine 'slim' has no browser, so every test run would stop.", {
            action: { kind: "fix_setup", label: "Let embry0 fix the setup" },
          }),
          row("first_task", "missing", "Give embry0 something small and low-risk. It opens a pull request for you to review.", {
            action: { kind: "create_issue", label: "Give embry0 a first task" },
          }),
          TRIGGER_UNKNOWN,
        ],
      }),
    );
    mockFetchSetupCost.mockReturnValue(new Promise(() => {}));
    await userEvent.setup().click(within(rowEl("testing")).getByRole("button", { name: "Let embry0 fix the setup" }));
    expect(screen.getByRole("dialog", { name: "Let embry0 set up testing" })).toBeInTheDocument();
    expect(within(rowEl("first_task")).getByRole("link", { name: "Give embry0 a first task" })).toHaveAttribute(
      "href",
      "/issues",
    );
  });

  it("opens the first pull request in a new tab once it exists", async () => {
    await renderWith(CONFIGURED);
    const open = within(rowEl("first_task")).getByRole("link", { name: "Open the pull request" });
    expect(open).toHaveAttribute("href", "https://github.com/acme/widgets/pull/7");
    expect(open).toHaveAttribute("target", "_blank");
  });

  it.each([
    ["missing", UNCONFIGURED],
    [
      "failed",
      readiness({
        rows: [
          CODE_DONE,
          VALUES_DONE,
          row("testing", "failed", "embry0 could not set up testing: the app did not start", {
            action: { kind: "start_setup", label: "Try again" },
          }),
          FIRST_WAITING,
          TRIGGER_UNKNOWN,
        ],
      }),
    ],
  ])("offers 'Skip testing for now' when testing is %s", async (_state, data) => {
    const user = userEvent.setup();
    mockSkipTesting.mockResolvedValue({ revision_id: 7 });
    await renderWith(data);
    const skip = within(rowEl("testing")).getByRole("button", { name: "Skip testing for now" });
    expect(skip.className).toContain("border-border-strong");
    await user.click(skip);
    await waitFor(() => expect(mockSkipTesting).toHaveBeenCalledWith("acme/widgets"));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Testing is off for now. embry0 opens pull requests without testing the app.",
      ),
    );
    await waitFor(() => expect(mockFetchReadiness).toHaveBeenCalledTimes(2));
  });

  it("says why skipping testing was refused", async () => {
    const user = userEvent.setup();
    mockSkipTesting.mockRejectedValue({
      response: { status: 409, data: { detail: "This repository already has a test setup." } },
    });
    await renderWith(UNCONFIGURED);
    await user.click(screen.getByRole("button", { name: "Skip testing for now" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not skip testing: This repository already has a test setup."),
    );
  });

  it("offers no skip once testing is set up, off or in progress", async () => {
    await renderWith(CONFIGURED);
    expect(screen.queryByRole("button", { name: "Skip testing for now" })).not.toBeInTheDocument();
  });

  it("links to Advanced settings and History at the foot", async () => {
    const card = await renderWith(UNCONFIGURED);
    expect(within(card).getByRole("link", { name: "Advanced settings" })).toHaveAttribute("href", "#advanced");
    expect(within(card).getByRole("link", { name: "History" })).toHaveAttribute("href", "#history");
  });

  it("says so when the status cannot load, and retries", async () => {
    const user = userEvent.setup();
    mockFetchReadiness.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(UNCONFIGURED);
    renderChecklist();
    expect(await screen.findByText("Could not load the setup status.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(UNCONFIGURED.summary)).toBeInTheDocument();
  });

  it("shows the card while the status loads", () => {
    mockFetchReadiness.mockReturnValue(new Promise(() => {}));
    renderChecklist();
    const card = screen.getByTestId("setup-checklist");
    expect(card).toHaveAttribute("aria-busy", "true");
    expect(within(card).getByRole("heading", { name: "Getting this repository ready" })).toBeInTheDocument();
  });
});

describe("readinessRefetchInterval", () => {
  it("polls only while embry0 is setting up testing", () => {
    expect(readinessRefetchInterval(IN_PROGRESS)).toBe(30_000);
    expect(readinessRefetchInterval(UNCONFIGURED)).toBe(false);
    expect(readinessRefetchInterval(REVIEW)).toBe(false);
    expect(readinessRefetchInterval(undefined)).toBe(false);
  });
});
