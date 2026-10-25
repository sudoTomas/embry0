import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import type { SetupCost } from "@/api/repoSetup";

const mockFetchSetupCost = vi.fn();
const mockStartSetup = vi.fn();
vi.mock("@/api/repoSetup", () => ({
  fetchSetupCost: (...a: unknown[]) => mockFetchSetupCost(...a),
  startSetup: (...a: unknown[]) => mockStartSetup(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SetupAgentDialog } from "../SetupAgentDialog";

const COST: SetupCost = {
  recorded_runs: 3,
  typical_cost_usd: 0.41,
  per_job_budget_usd: 10,
  model: "medium-model",
  paid_from: "Team AI account",
};

const GOLD = /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/;
const goldElements = (root: HTMLElement) =>
  [...root.querySelectorAll<HTMLElement>("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));

function renderDialog() {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <SetupAgentDialog repo="acme/widgets" onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose, invalidate };
}

const dialog = () => screen.getByRole("dialog", { name: "Let embry0 set up testing" });
const startButton = () => within(dialog()).getByRole("button", { name: "Start" });

beforeEach(() => {
  mockFetchSetupCost.mockReset();
  mockStartSetup.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});

describe("SetupAgentDialog", () => {
  it("says what embry0 will do, what it will not touch, and what past runs cost", async () => {
    mockFetchSetupCost.mockResolvedValue(COST);
    renderDialog();
    expect(within(dialog()).getByRole("heading", { name: "Let embry0 set up testing" })).toBeInTheDocument();
    expect(dialog()).toHaveTextContent(
      "What embry0 will do: copy your code to a private test computer, read it, write a test setup, then start your app there and check that it answers. It tries up to three times.",
    );
    expect(dialog()).toHaveTextContent(
      "What it will not do: change your code, open a pull request, or change any setting. You get a proposal to review first.",
    );
    expect(await screen.findByText("Setup runs recorded on this embry0: 3, typical cost $0.41.")).toBeInTheDocument();
    expect(screen.getByText("Your per-job budget is $10.00.")).toBeInTheDocument();
    expect(screen.getByText("Paid from: Team AI account")).toBeInTheDocument();
    expect(mockFetchSetupCost).toHaveBeenCalledWith("acme/widgets");
  });

  it("says when no setup run is recorded, and leaves out who pays when that is not known", async () => {
    mockFetchSetupCost.mockResolvedValue({ ...COST, recorded_runs: 0, typical_cost_usd: null, paid_from: null });
    renderDialog();
    expect(await screen.findByText("No setup run is recorded here yet.")).toBeInTheDocument();
    expect(screen.getByText("Your per-job budget is $10.00.")).toBeInTheDocument();
    expect(screen.queryByText(/Paid from/)).not.toBeInTheDocument();
    expect(screen.queryByText(/typical cost/)).not.toBeInTheDocument();
  });

  it("spends its one gold on Start and keeps Cancel quiet", async () => {
    mockFetchSetupCost.mockResolvedValue(COST);
    renderDialog();
    await screen.findByText(/Setup runs recorded/);
    expect(goldElements(dialog())).toEqual([startButton()]);
    expect(within(dialog()).getByRole("button", { name: "Cancel" }).className).toContain("border-border-strong");
  });

  it("starts nothing on Cancel", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockResolvedValue(COST);
    const { onClose } = renderDialog();
    await screen.findByText(/Setup runs recorded/);
    await user.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(mockStartSetup).not.toHaveBeenCalled();
  });

  it("starts nothing on Escape", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockResolvedValue(COST);
    const { onClose } = renderDialog();
    await screen.findByText(/Setup runs recorded/);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    expect(mockStartSetup).not.toHaveBeenCalled();
  });

  it("starts the run only from Start, then closes and refreshes the setup status", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockResolvedValue(COST);
    mockStartSetup.mockResolvedValue({ proposal_id: "prop-abc", job_id: "job-1" });
    const { onClose, invalidate } = renderDialog();
    await screen.findByText(/Setup runs recorded/);
    expect(mockStartSetup).not.toHaveBeenCalled();

    await user.click(startButton());
    await waitFor(() => expect(mockStartSetup).toHaveBeenCalledTimes(1));
    expect(mockStartSetup).toHaveBeenCalledWith("acme/widgets", {});
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith("embry0 is setting up testing. You can leave this page.");
    for (const queryKey of [
      ["repo-readiness", "acme/widgets"],
      ["repo-history", "acme/widgets"],
      ["repo-setup-proposal", "acme/widgets"],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
  });

  it("shows why a start was refused and stays open", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockResolvedValue(COST);
    mockStartSetup.mockRejectedValue({
      response: { status: 409, data: { detail: "embry0 is already setting up testing for this repository." } },
    });
    const { onClose } = renderDialog();
    await screen.findByText(/Setup runs recorded/);
    await user.click(startButton());
    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(
      "embry0 is already setting up testing for this repository.",
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("holds Start until the cost facts are shown", () => {
    mockFetchSetupCost.mockReturnValue(new Promise(() => {}));
    renderDialog();
    expect(startButton()).toBeDisabled();
    expect(within(dialog()).getByTestId("setup-cost")).toHaveAttribute("aria-busy", "true");
  });

  it("says when the cost facts cannot load, keeps Start held, and retries", async () => {
    const user = userEvent.setup();
    mockFetchSetupCost.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(COST);
    renderDialog();
    expect(await screen.findByText("Could not load what setup runs cost here.")).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    await user.click(within(dialog()).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Setup runs recorded on this embry0: 3, typical cost $0.41.")).toBeInTheDocument();
    expect(startButton()).toBeEnabled();
  });
});
