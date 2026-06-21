import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ProposalsPage talks to the companion agent (not the orchestrator) through
// `src/api/agent.ts`. Tests stub the four touched functions and assert the
// page renders the list, fires the right mutation per action, and reflects
// optimistic state on the row immediately (before the mutation resolves).

const mockFetchProposals = vi.fn();
const mockShipProposal = vi.fn();
const mockRescoreProposal = vi.fn();
const mockBatchShipProposals = vi.fn();

vi.mock("@/api/agent", () => ({
  fetchProposals: () => mockFetchProposals(),
  shipProposal: (id: string) => mockShipProposal(id),
  rescoreProposal: (id: string) => mockRescoreProposal(id),
  batchShipProposals: (ids: string[]) => mockBatchShipProposals(ids),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { ProposalsPage } from "../ProposalsPage";
import type { AgentProposal } from "@/api/agent";

const P_CRITICAL: AgentProposal = {
  id: "p-1",
  title: "Bump axios past CVE-2024-1234",
  repo: "ormus/embry0",
  severity: 9,
  status: "pending",
};
const P_LOW: AgentProposal = {
  id: "p-2",
  title: "Add Prettier ignore for /dist",
  repo: "ormus/embry0",
  severity: 2,
  status: "pending",
};

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  mockFetchProposals.mockReset();
  mockShipProposal.mockReset();
  mockRescoreProposal.mockReset();
  mockBatchShipProposals.mockReset();
});

describe("ProposalsPage", () => {
  it("shows empty-state copy when the proposals list is empty", async () => {
    mockFetchProposals.mockResolvedValue([]);
    renderWithClient(<ProposalsPage />);
    expect(await screen.findByText(/no proposals/i)).toBeInTheDocument();
  });

  it("renders one row per proposal with title, repo, and severity", async () => {
    mockFetchProposals.mockResolvedValue([P_CRITICAL, P_LOW]);
    renderWithClient(<ProposalsPage />);
    expect(await screen.findByTestId("proposal-row-p-1")).toHaveTextContent(
      "Bump axios past CVE-2024-1234",
    );
    expect(screen.getByTestId("proposal-row-p-1")).toHaveTextContent("ormus/embry0");
    expect(screen.getByTestId("proposal-row-p-1")).toHaveTextContent("9");
    expect(screen.getByTestId("proposal-row-p-2")).toHaveTextContent(
      "Add Prettier ignore for /dist",
    );
  });

  it("ship action calls shipProposal with the row id", async () => {
    mockFetchProposals.mockResolvedValue([P_CRITICAL]);
    mockShipProposal.mockResolvedValue({ ...P_CRITICAL, status: "shipped" });
    renderWithClient(<ProposalsPage />);
    fireEvent.click(await screen.findByLabelText("Ship p-1"));
    await waitFor(() => expect(mockShipProposal).toHaveBeenCalledWith("p-1"));
  });

  it("rescore action calls rescoreProposal with the row id", async () => {
    mockFetchProposals.mockResolvedValue([P_CRITICAL]);
    mockRescoreProposal.mockResolvedValue({ ...P_CRITICAL, severity: 10 });
    renderWithClient(<ProposalsPage />);
    fireEvent.click(await screen.findByLabelText("Rescore p-1"));
    await waitFor(() => expect(mockRescoreProposal).toHaveBeenCalledWith("p-1"));
  });

  it("batch-ship action calls batchShipProposals with selected ids", async () => {
    mockFetchProposals.mockResolvedValue([P_CRITICAL, P_LOW]);
    mockBatchShipProposals.mockResolvedValue({ shipped: ["p-1"] });
    renderWithClient(<ProposalsPage />);

    fireEvent.click(await screen.findByLabelText("Select p-1"));
    fireEvent.click(screen.getByRole("button", { name: /ship selected/i }));

    await waitFor(() => expect(mockBatchShipProposals).toHaveBeenCalledWith(["p-1"]));
  });

  it("ship action optimistically marks the row shipped before the mutation resolves", async () => {
    mockFetchProposals.mockResolvedValue([P_CRITICAL]);
    // Hold the mutation in-flight so the optimistic state is observable.
    let resolveShip: (v: AgentProposal) => void = () => {};
    mockShipProposal.mockImplementation(
      () =>
        new Promise<AgentProposal>((resolve) => {
          resolveShip = resolve;
        }),
    );

    renderWithClient(<ProposalsPage />);
    fireEvent.click(await screen.findByLabelText("Ship p-1"));

    // Before the mutation resolves, the row must already reflect the new status.
    await waitFor(() =>
      expect(screen.getByTestId("proposal-row-p-1")).toHaveTextContent(/shipped/i),
    );

    resolveShip({ ...P_CRITICAL, status: "shipped" });
  });
});
