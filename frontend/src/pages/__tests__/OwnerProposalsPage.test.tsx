import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OwnerProposal } from "@/api/ownerProposals";

const mockUseOwnerProposals = vi.fn();
const mutate = vi.fn();
vi.mock("@/hooks/useOwnerProposals", () => ({
  useOwnerProposals: (filters: unknown) => mockUseOwnerProposals(filters),
  useAnswerOwnerProposal: () => ({ mutate, isPending: false }),
}));
const admin = { current: true };
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => admin.current,
  useAuthStore: (select: (s: { user: { email: string } }) => unknown) => select({ user: { email: "owner@example.com" } }),
}));

import { OwnerProposalsPage } from "../OwnerProposalsPage";

const FINDING: OwnerProposal = {
  id: "finding:abc",
  kind: "finding",
  repo: "acme/docs",
  owners: ["octo"],
  title: "AGENTS.md:3: Run make check",
  summary: 'We found this in AGENTS.md:3: "Run make check". Is that what you want it to say?',
  details: "Proposed fix: say make test",
  action: "Open a pull request fixing AGENTS.md",
  url: null,
  created_at: "2026-10-24T12:00:00+00:00",
  path: "AGENTS.md",
  line: 3,
};

function renderPage(path = "/owner-proposals") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OwnerProposalsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mutate.mockReset();
  admin.current = true;
  mockUseOwnerProposals.mockReturnValue({ data: [FINDING] });
});

describe("OwnerProposalsPage (owner proposals, spec 2026-10-24)", () => {
  it("shows each proposal's question, owners and what yes does, details on demand", async () => {
    renderPage();
    const card = screen.getByTestId("owner-proposal");
    expect(card).toHaveTextContent("Context finding");
    expect(card).toHaveTextContent('We found this in AGENTS.md:3: "Run make check". Is that what you want it to say?');
    expect(card).toHaveTextContent("Owners: octo");
    expect(card).toHaveTextContent("Yes: Open a pull request fixing AGENTS.md");
    expect(screen.queryByTestId("owner-proposal-details")).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "Details" }));
    expect(screen.getByTestId("owner-proposal-details")).toHaveTextContent("Proposed fix: say make test");
  });

  it("filters by owner and kind from the address", () => {
    renderPage("/owner-proposals?owner=octo&kind=finding");
    expect(mockUseOwnerProposals).toHaveBeenCalledWith({ owner: "octo", kind: "finding" });
  });

  it("answers as the person signed in: start, not this with a reason, later in a week", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("it says what I want");
    renderPage();
    const card = screen.getByTestId("owner-proposal");
    await userEvent.click(within(card).getByRole("button", { name: "Start" }));
    await userEvent.click(within(card).getByRole("button", { name: "Not this" }));
    await userEvent.click(within(card).getByRole("button", { name: "Later" }));
    const calls = mutate.mock.calls.map(([args]) => [args.id, args.decision, args.body.by, args.body.reason ?? null]);
    expect(calls).toEqual([
      ["finding:abc", "accept", "owner@example.com", null],
      ["finding:abc", "decline", "owner@example.com", "it says what I want"],
      ["finding:abc", "snooze", "owner@example.com", null],
    ]);
    expect(Date.parse(mutate.mock.calls[2][0].body.until)).toBeGreaterThan(Date.now() + 6 * 86_400_000);
    prompt.mockRestore();
  });

  it("offers no answers to a session that is not admin, and says when nothing is asked", () => {
    admin.current = false;
    const { unmount } = renderPage();
    expect(within(screen.getByTestId("owner-proposal")).queryByRole("button", { name: "Start" })).toBeNull();
    unmount();
    mockUseOwnerProposals.mockReturnValue({ data: [] });
    renderPage("/owner-proposals?owner=octo");
    expect(screen.getByText("Nothing to ask octo right now.")).toBeInTheDocument();
  });
});
