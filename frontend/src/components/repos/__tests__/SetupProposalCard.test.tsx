import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import type { QaCheckResult, SetupProposal } from "@/api/repoSetup";

const mockFetchProposal = vi.fn();
const mockApplyProposal = vi.fn();
const mockRejectProposal = vi.fn();
vi.mock("@/api/repoSetup", () => ({
  fetchProposal: (...a: unknown[]) => mockFetchProposal(...a),
  applyProposal: (...a: unknown[]) => mockApplyProposal(...a),
  rejectProposal: (...a: unknown[]) => mockRejectProposal(...a),
}));
const mockUndoChange = vi.fn();
vi.mock("@/api/repoHistory", () => ({
  fetchHistory: vi.fn(),
  undoChange: (...a: unknown[]) => mockUndoChange(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SetupProposalCard } from "../SetupProposalCard";
import { proposalRefetchInterval } from "@/hooks/useSetupProposal";

const DOCKER = "Testing will not start until an admin grants Docker access.";
const DEPLOYED = "docs is tested where it already runs, at https://docs.example.com. Tests there use real data.";
const LOGIN = "The setup signs in with QA_LOGIN_STATE, which has no value yet.";
const STALE = "Someone changed the test setup after embry0 made this proposal. Run embry0 again for a fresh one.";
const APPLIED =
  "Testing is set up. It applies from the next test run, including jobs already running that have not reached their test step.";

const CHECK: QaCheckResult = {
  ok: true,
  problems: [],
  warnings: [
    { code: "deployed_target", plain: DEPLOYED, confirm: true },
    { code: "needs_docker", plain: DOCKER, confirm: true },
    { code: "login_value_missing", plain: LOGIN, confirm: false },
  ],
  summary: {
    when: "auto",
    when_plain: "When a change touches the app",
    apps: [
      {
        name: "web",
        target: "managed",
        start: "npm ci && npm run dev",
        address: "http://localhost:5173",
        address_is_local: true,
        criteria: ["The home page loads", "Signing in works"],
        guardrails: 0,
        machine: "qa-external",
        machine_has_browser: true,
      },
      {
        name: "docs",
        target: "deployed",
        start: null,
        address: "https://docs.example.com",
        address_is_local: false,
        criteria: ["The docs index loads"],
        guardrails: 1,
        machine: "qa-external",
        machine_has_browser: true,
      },
    ],
  },
};

const PENDING: SetupProposal = {
  id: "prop-abc",
  repo: "acme/widgets",
  status: "pending",
  job_id: "job-123",
  job_status: "completed",
  created_by: "alex@example.com",
  created_at: "2026-10-18T10:00:00Z",
  decided_by: null,
  decided_at: null,
  notes_md: "Found a **Vite** app in the web folder.",
  failure: "",
  smoke: { results: { web: "passed", docs: "passed" }, failures: [] },
  proposed_yaml: "version: 2\napps:\n  web:\n    boot_command: npm ci && npm run dev\n",
  current_yaml: null,
  check: CHECK,
};

/** A setup with one app and no warnings to tick. */
const SIMPLE: SetupProposal = {
  ...PENDING,
  check: { ...CHECK, warnings: [], summary: { ...CHECK.summary!, apps: [CHECK.summary!.apps[0]] } },
  smoke: { results: { web: "passed" }, failures: [] },
};

const FAILED: SetupProposal = {
  ...PENDING,
  status: "failed",
  failure: "The app did not start: missing script dev",
  notes_md: "Tried `npm run dev` three times.",
  proposed_yaml: null,
  check: null,
  smoke: null,
};

const GOLD = /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/;
const goldElements = (root: HTMLElement) =>
  [...root.querySelectorAll<HTMLElement>("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));

function renderCard(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const props = { onClose: vi.fn(), onTryAgain: vi.fn(), onSkip: vi.fn() };
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SetupProposalCard repo="acme/widgets" skipping={false} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return props;
}

async function renderWith(proposal: SetupProposal | null) {
  mockFetchProposal.mockResolvedValue(proposal);
  const props = renderCard();
  await waitFor(() => expect(screen.getByTestId("setup-proposal")).not.toHaveAttribute("aria-busy", "true"));
  return props;
}

const card = () => screen.getByTestId("setup-proposal");
const applyButton = () => within(card()).getByRole("button", { name: "Apply this setup" });

async function tickAll(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("checkbox", { name: DEPLOYED }));
  await user.click(screen.getByRole("checkbox", { name: DOCKER }));
}

beforeEach(() => {
  mockFetchProposal.mockReset();
  mockApplyProposal.mockReset();
  mockRejectProposal.mockReset();
  mockUndoChange.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("SetupProposalCard", () => {
  it("says in plain words what the setup starts, where it tests and what it checks", async () => {
    await renderWith(PENDING);
    expect(mockFetchProposal).toHaveBeenCalledWith("acme/widgets");
    expect(within(card()).getByRole("heading")).toHaveTextContent(
      "embry0 has a test setup for you to review. Nothing has changed yet.",
    );
    expect(card()).toHaveTextContent(
      "Starts web with npm ci && npm run dev. Tests it at http://localhost:5173 on embry0's test computer.",
    );
    expect(card()).toHaveTextContent("Tests docs at https://docs.example.com.");
    expect(card()).toHaveTextContent("After each change it will check:");
    const checks = within(card()).getByTestId("setup-proposal-criteria");
    expect(within(checks).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "web: The home page loads",
      "web: Signing in works",
      "docs: The docs index loads",
    ]);
  });

  it("shows what embry0 proved apart from what it asks you to accept", async () => {
    await renderWith(PENDING);
    const proven = within(card()).getByTestId("setup-proposal-proven");
    expect(proven).toHaveTextContent("embry0 started web on its test computer and the check address answered.");
    expect(proven).toHaveTextContent("embry0 checked docs where it already runs, and the check address answered.");
    expect(within(proven).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("claims nothing for an app whose check did not pass", async () => {
    await renderWith({ ...PENDING, smoke: { results: { web: "passed", docs: "failed" }, failures: [] } });
    const proven = within(card()).getByTestId("setup-proposal-proven");
    expect(proven).toHaveTextContent("embry0 started web");
    expect(proven).not.toHaveTextContent("docs");
  });

  it("lists what still needs you, with a place to add it", async () => {
    await renderWith(PENDING);
    const needs = within(card()).getByTestId("setup-proposal-needs-you");
    expect(needs).toHaveTextContent("Still needs you");
    expect(needs).toHaveTextContent(LOGIN);
    expect(within(needs).getByRole("link", { name: "Add it" })).toHaveAttribute(
      "href",
      "/environments?repo=acme%2Fwidgets",
    );
  });

  it("keeps the agent's notes and the raw text behind disclosures", async () => {
    await renderWith(PENDING);
    const why = within(card()).getByText("Why embry0 chose this").closest("details")!;
    expect(why).not.toHaveAttribute("open");
    expect(within(why).getByText("Vite").tagName).toBe("STRONG");

    const technical = within(card()).getByText("Show the technical version").closest("details")!;
    expect(technical).not.toHaveAttribute("open");
    expect(within(technical).getByText("Now")).toBeInTheDocument();
    expect(within(technical).getByText("No test setup yet")).toBeInTheDocument();
    expect(within(technical).getByText("Proposed")).toBeInTheDocument();
    expect(within(technical).getByTestId("setup-proposal-proposed").textContent).toBe(PENDING.proposed_yaml);
  });

  it("shows the current setup beside the proposed one when there is one", async () => {
    await renderWith({ ...PENDING, current_yaml: "version: 2\nqa_required: never\n" });
    expect(within(card()).getByTestId("setup-proposal-current").textContent).toBe("version: 2\nqa_required: never\n");
    expect(within(card()).queryByText("No test setup yet")).not.toBeInTheDocument();
  });

  it("keeps Apply disabled until every warning is ticked", async () => {
    const user = userEvent.setup();
    await renderWith(PENDING);
    expect(applyButton()).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: DEPLOYED }));
    expect(applyButton()).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: DOCKER }));
    expect(applyButton()).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: DOCKER }));
    expect(applyButton()).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: LOGIN })).not.toBeInTheDocument();
  });

  it("spends its one gold on Apply, with Not now and Turn this down quiet", async () => {
    await renderWith(PENDING);
    expect(goldElements(card())).toEqual([applyButton()]);
    expect(within(card()).getByRole("button", { name: "Not now" }).className).toContain("border-border-strong");
    expect(within(card()).getByRole("button", { name: "Turn this down" }).className).toContain("text-fg-secondary");
  });

  it("applies with the ticked warnings, refreshes the page and offers Undo in the toast", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValue(PENDING);
    mockApplyProposal.mockResolvedValue({ revision_id: 12 });
    mockUndoChange.mockResolvedValue({ revision_id: 13 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { onClose } = renderCard(client);
    await screen.findByText(DEPLOYED);
    await tickAll(user);
    await user.click(applyButton());

    await waitFor(() =>
      expect(mockApplyProposal).toHaveBeenCalledWith("acme/widgets", "prop-abc", ["deployed_target", "needs_docker"]),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(APPLIED, {
        action: { label: "Undo", onClick: expect.any(Function) },
      }),
    );
    expect(onClose).toHaveBeenCalled();
    for (const queryKey of [
      ["repo-readiness", "acme/widgets"],
      ["repo-history", "acme/widgets"],
      ["repo-setup-proposal", "acme/widgets"],
      ["qa-config", "acme/widgets"],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }

    const action = vi.mocked(toast.success).mock.calls[0][1]?.action;
    if (!action || typeof action !== "object" || !("onClick" in action)) throw new Error("the toast has no Undo action");
    action.onClick({} as Parameters<typeof action.onClick>[0]);
    await waitFor(() => expect(mockUndoChange).toHaveBeenCalledWith("acme/widgets", 12));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Change undone."));
  });

  it("applies a setup with nothing to tick straight away", async () => {
    const user = userEvent.setup();
    mockApplyProposal.mockResolvedValue({ revision_id: 12 });
    await renderWith(SIMPLE);
    expect(within(card()).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(applyButton());
    await waitFor(() => expect(mockApplyProposal).toHaveBeenCalledWith("acme/widgets", "prop-abc", []));
  });

  it("says the setup is stale when someone changed it first, and keeps Apply disabled", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValueOnce(PENDING).mockResolvedValue({ ...PENDING, status: "stale" });
    mockApplyProposal.mockRejectedValue({ response: { status: 409, data: { detail: STALE } } });
    const { onClose } = renderCard();
    await screen.findByText(DEPLOYED);
    await tickAll(user);
    await user.click(applyButton());

    expect(await within(card()).findByRole("alert")).toHaveTextContent(STALE);
    expect(applyButton()).toBeDisabled();
    await waitFor(() => expect(mockFetchProposal).toHaveBeenCalledTimes(2));
    expect(within(card()).getByRole("alert")).toHaveTextContent(STALE);
    expect(applyButton()).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("opens a stale proposal with the run-again text and Apply disabled", async () => {
    await renderWith({ ...SIMPLE, status: "stale" });
    expect(within(card()).getByRole("alert")).toHaveTextContent(STALE);
    expect(applyButton()).toBeDisabled();
  });

  it("asks for the ticks again when the server finds a warning that was not ticked", async () => {
    const user = userEvent.setup();
    mockApplyProposal.mockRejectedValue({ response: { status: 422, data: { detail: { missing: ["needs_docker"] } } } });
    await renderWith(PENDING);
    await tickAll(user);
    await user.click(applyButton());
    expect(await within(card()).findByRole("alert")).toHaveTextContent("Tick each warning shown before you apply.");
  });

  it("shows the problems when the server finds the setup cannot run", async () => {
    const user = userEvent.setup();
    mockApplyProposal.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [{ where: "app web", plain: "The work machine 'slim' has no browser, so every test run would stop.", fix: "Use 'qa-external'." }],
        },
      },
    });
    await renderWith(SIMPLE);
    await user.click(applyButton());
    expect(await within(card()).findByRole("alert")).toHaveTextContent(
      "The work machine 'slim' has no browser, so every test run would stop.",
    );
  });

  it("will not apply a setup the free check says would stop every run", async () => {
    await renderWith({
      ...SIMPLE,
      check: {
        ...SIMPLE.check!,
        ok: false,
        problems: [
          {
            where: "app web",
            plain: "The work machine 'slim' has no browser, so every test run would stop.",
            fix: "Use 'qa-external'.",
          },
        ],
      },
    });
    expect(card()).toHaveTextContent("The work machine 'slim' has no browser, so every test run would stop. Use 'qa-external'.");
    expect(applyButton()).toBeDisabled();
  });

  it("collapses on Not now and sends nothing", async () => {
    const user = userEvent.setup();
    const { onClose } = await renderWith(PENDING);
    await user.click(within(card()).getByRole("button", { name: "Not now" }));
    expect(onClose).toHaveBeenCalled();
    expect(mockApplyProposal).not.toHaveBeenCalled();
    expect(mockRejectProposal).not.toHaveBeenCalled();
  });

  it("turns the setup down and refreshes the status", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockResolvedValue(PENDING);
    mockRejectProposal.mockResolvedValue({});
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { onClose } = renderCard(client);
    await screen.findByText(DEPLOYED);
    await user.click(within(card()).getByRole("button", { name: "Turn this down" }));
    await waitFor(() => expect(mockRejectProposal).toHaveBeenCalledWith("acme/widgets", "prop-abc"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith("Setup turned down. Nothing was changed.");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repo-readiness", "acme/widgets"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repo-history", "acme/widgets"] });
    expect(mockApplyProposal).not.toHaveBeenCalled();
  });

  it("says why a failed run stopped, with its notes, Try again and Skip", async () => {
    const user = userEvent.setup();
    const { onTryAgain, onSkip } = await renderWith(FAILED);
    expect(card()).toHaveTextContent("embry0 could not set up testing: The app did not start: missing script dev");
    const notes = within(card()).getByText("What embry0 noticed").closest("details")!;
    expect(within(notes).getByText("npm run dev").tagName).toBe("CODE");
    expect(within(card()).queryByRole("button", { name: "Apply this setup" })).not.toBeInTheDocument();

    const tryAgain = within(card()).getByRole("button", { name: "Try again" });
    expect(goldElements(card())).toEqual([tryAgain]);
    await user.click(tryAgain);
    expect(onTryAgain).toHaveBeenCalled();

    const skip = within(card()).getByRole("button", { name: "Skip testing for now" });
    expect(skip.className).toContain("border-border-strong");
    await user.click(skip);
    expect(onSkip).toHaveBeenCalled();
  });

  it("says when embry0 is still working on the setup", async () => {
    await renderWith({ ...PENDING, status: "running", job_status: "running", check: null, proposed_yaml: null });
    expect(card()).toHaveTextContent("embry0 is still working on a test setup. You can leave this page.");
    expect(goldElements(card())).toEqual([]);
  });

  it.each([
    ["no proposal", null],
    ["an applied one", { ...PENDING, status: "applied" as const }],
    ["a turned-down one", { ...PENDING, status: "rejected" as const }],
  ])("says there is nothing to review for %s", async (_name, proposal) => {
    await renderWith(proposal);
    expect(card()).toHaveTextContent("There is no test setup waiting for review.");
    expect(within(card()).queryByRole("button", { name: "Apply this setup" })).not.toBeInTheDocument();
  });

  it("shows the card while the proposal loads", () => {
    mockFetchProposal.mockReturnValue(new Promise(() => {}));
    renderCard();
    expect(card()).toHaveAttribute("aria-busy", "true");
  });

  it("says so when the proposal cannot load, and retries", async () => {
    const user = userEvent.setup();
    mockFetchProposal.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(PENDING);
    renderCard();
    expect(await screen.findByText("Could not load the setup proposal.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(DEPLOYED)).toBeInTheDocument();
  });
});

describe("proposalRefetchInterval", () => {
  it("polls only while embry0 is still working", () => {
    expect(proposalRefetchInterval({ ...PENDING, status: "running" })).toBe(30_000);
    expect(proposalRefetchInterval(PENDING)).toBe(false);
    expect(proposalRefetchInterval(null)).toBe(false);
    expect(proposalRefetchInterval(undefined)).toBe(false);
  });
});
