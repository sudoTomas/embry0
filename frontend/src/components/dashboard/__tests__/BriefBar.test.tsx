import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

type FakeQuery<T> = { data: T | undefined; isPending: boolean; isError: boolean };
const ready = <T,>(data: T): FakeQuery<T> => ({ data, isPending: false, isError: false });

const mockQueue = vi.fn();
const mockProposals = vi.fn();
const mockApprovals = vi.fn();
vi.mock("@/hooks/useQueue", () => ({ useQueue: () => mockQueue() }));
vi.mock("@/hooks/useProposals", () => ({ useProposals: () => mockProposals() }));
vi.mock("@/hooks/useMergeApprovals", () => ({ usePendingMergeApprovals: () => mockApprovals() }));

import { BriefBar } from "../BriefBar";

function renderBar() {
  return render(
    <MemoryRouter>
      <BriefBar />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockQueue.mockReturnValue(ready({ depth: 9, pending: 7, running: 4, awaiting_input: 2, paused: 1 }));
  mockProposals.mockReturnValue(
    ready({ cases: [{ id: "c1", origin: "org" }, { id: "c2", origin: "org" }, { id: "c3", origin: "watcher" }] }),
  );
  mockApprovals.mockReturnValue(ready([{ id: "a1", status: "pending" }]));
});

describe("BriefBar", () => {
  it("says what waits for a person and what runs, each number linked to its page", () => {
    renderBar();
    expect(screen.getByTestId("brief-awaiting")).toHaveTextContent("2 awaiting input");
    expect(screen.getByTestId("brief-awaiting")).toHaveAttribute("href", "/console");
    expect(screen.getByTestId("brief-paused")).toHaveTextContent("1 paused");
    expect(screen.getByTestId("brief-approvals")).toHaveTextContent("1 merge approval asked");
    expect(screen.getByTestId("brief-org-needs")).toHaveTextContent("2 organization needs");
    expect(screen.getByTestId("brief-org-needs")).toHaveAttribute("href", "/orgs");
    expect(screen.getByTestId("brief-drafts")).toHaveTextContent("1 draft ticket to decide");
    expect(screen.getByTestId("brief-drafts")).toHaveAttribute("href", "/issues");
    expect(screen.getByTestId("brief-running")).toHaveTextContent("4 running");
    expect(screen.getByTestId("brief-queued")).toHaveTextContent("7 queued");
    expect(screen.getByTestId("brief-running")).toHaveAttribute("href", "/jobs");
  });

  it("colours what needs a person amber and live work blue", () => {
    renderBar();
    expect(screen.getByTestId("brief-awaiting").className).toContain("text-warning");
    expect(screen.getByTestId("brief-running").className).toContain("text-info");
  });

  it("mutes running and queued at zero instead of colouring them blue", () => {
    mockQueue.mockReturnValue(ready({ depth: 0, pending: 0, running: 0, awaiting_input: 0, paused: 0 }));
    renderBar();
    expect(screen.getByTestId("brief-running").className).toContain("text-fg-muted");
    expect(screen.getByTestId("brief-running").className).not.toContain("text-info");
    expect(screen.getByTestId("brief-queued").className).toContain("text-fg-muted");
  });

  it("wraps below sm instead of hiding items behind a sideways scroll", () => {
    renderBar();
    const bar = screen.getByTestId("brief-bar");
    expect(bar.className).toContain("flex-wrap");
    expect(bar.className).toContain("sm:flex-nowrap");
  });

  it("leaves out a zero, and says so when nothing waits for anyone", () => {
    mockQueue.mockReturnValue(ready({ depth: 0, pending: 0, running: 0, awaiting_input: 0, paused: 0 }));
    mockProposals.mockReturnValue(ready({ cases: [] }));
    mockApprovals.mockReturnValue(ready([]));
    renderBar();
    expect(screen.queryByTestId("brief-awaiting")).toBeNull();
    expect(screen.queryByTestId("brief-approvals")).toBeNull();
    expect(screen.getByText("Nothing waits for you")).toBeInTheDocument();
    expect(screen.getByTestId("brief-running")).toHaveTextContent("0 running");
  });

  it("drops the merge asks when the deployment has merge approvals off", () => {
    mockApprovals.mockReturnValue(ready(null));
    renderBar();
    expect(screen.queryByTestId("brief-approvals")).toBeNull();
    expect(screen.getByTestId("brief-awaiting")).toBeInTheDocument();
  });

  it("opens with today's date", () => {
    renderBar();
    const weekday = new Date().toLocaleDateString("en-US", { weekday: "long" });
    expect(screen.getByTestId("brief-bar")).toHaveTextContent(weekday);
  });
});
