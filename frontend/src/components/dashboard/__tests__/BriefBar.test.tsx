import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { TWEEN_MS } from "@/hooks/useTweenedNumber";

type FakeQuery<T> = { data: T | undefined; isPending: boolean; isError: boolean };
const ready = <T,>(data: T): FakeQuery<T> => ({ data, isPending: false, isError: false });

const mockQueue = vi.fn();
const mockProposals = vi.fn();
const mockApprovals = vi.fn();
vi.mock("@/hooks/useQueue", () => ({ useQueue: () => mockQueue() }));
vi.mock("@/hooks/useProposals", () => ({ useProposals: () => mockProposals() }));
vi.mock("@/hooks/useMergeApprovals", () => ({ usePendingMergeApprovals: () => mockApprovals() }));
const mockSetups = vi.fn();
vi.mock("@/hooks/useSetupProposal", () => ({ usePendingSetupProposals: () => mockSetups() }));

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
  mockSetups.mockReturnValue(ready([]));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
});

const pending = (id: string, repo: string) => ({ id, repo, created_at: "2026-10-18T10:00:00Z" });

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

  it("writes what needs a person in the body colour, gold only on hover, and live work blue", () => {
    renderBar();
    const awaiting = screen.getByTestId("brief-awaiting").className;
    expect(awaiting).toContain("text-fg");
    expect(awaiting).toContain("hover:text-primary");
    expect(awaiting).not.toContain("text-warning");
    expect(awaiting).not.toMatch(/(^| )text-primary/);
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

  it("counts one test setup to review and links to its repository", () => {
    mockSetups.mockReturnValue(ready([pending("prop-abc", "acme/widgets")]));
    renderBar();
    const setups = screen.getByTestId("brief-setups");
    expect(setups).toHaveTextContent("1 test setup to review");
    expect(setups).toHaveAttribute("href", "/repos/acme/widgets");
  });

  it("links a nested GitLab repository by its whole path", () => {
    mockSetups.mockReturnValue(ready([pending("prop-abc", "group/sub/project")]));
    renderBar();
    expect(screen.getByTestId("brief-setups")).toHaveAttribute("href", "/repos/group/sub/project");
  });

  it("counts several test setups to review and links to the repositories list", () => {
    mockSetups.mockReturnValue(ready([pending("prop-abc", "acme/widgets"), pending("prop-def", "acme/gadgets")]));
    renderBar();
    const setups = screen.getByTestId("brief-setups");
    expect(setups).toHaveTextContent("2 test setups to review");
    expect(setups).toHaveAttribute("href", "/repos");
  });

  it("writes test setups to review in the body colour, gold only on hover", () => {
    mockSetups.mockReturnValue(ready([pending("prop-abc", "acme/widgets")]));
    renderBar();
    const setups = screen.getByTestId("brief-setups").className;
    expect(setups).toContain("text-fg");
    expect(setups).toContain("hover:text-primary");
    expect(setups).not.toContain("text-warning");
    expect(setups).not.toMatch(/(^| )text-primary/);
  });

  it("leaves test setups out at zero, and when their source fails", () => {
    renderBar();
    expect(screen.queryByTestId("brief-setups")).toBeNull();
    cleanup();
    mockSetups.mockReturnValue({ data: undefined, isPending: false, isError: true });
    renderBar();
    expect(screen.queryByTestId("brief-setups")).toBeNull();
    expect(screen.getByTestId("brief-awaiting")).toBeInTheDocument();
  });

  it("says something waits when the only item is a test setup to review", () => {
    mockQueue.mockReturnValue(ready({ depth: 0, pending: 0, running: 0, awaiting_input: 0, paused: 0 }));
    mockProposals.mockReturnValue(ready({ cases: [] }));
    mockApprovals.mockReturnValue(ready([]));
    mockSetups.mockReturnValue(ready([pending("prop-abc", "acme/widgets")]));
    renderBar();
    expect(screen.queryByText("Nothing waits for you")).not.toBeInTheDocument();
    expect(screen.getByTestId("brief-setups")).toHaveTextContent("1 test setup to review");
  });

  it("opens with today's date", () => {
    renderBar();
    const weekday = new Date().toLocaleDateString("en-US", { weekday: "long" });
    expect(screen.getByTestId("brief-bar")).toHaveTextContent(weekday);
  });

  it("shows a count as it is on first paint, then travels to a changed one and bumps it once", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    let queued: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => queued.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const frame = (now: number) => {
      const due = queued;
      queued = [];
      act(() => due.forEach((cb) => cb(now)));
    };
    const animate = vi.fn();
    HTMLElement.prototype.animate = animate;

    const { rerender } = renderBar();
    expect(screen.getByTestId("brief-running")).toHaveTextContent("4 running");
    expect(animate).not.toHaveBeenCalled();

    mockQueue.mockReturnValue(ready({ depth: 10, pending: 7, running: 5, awaiting_input: 2, paused: 1 }));
    rerender(
      <MemoryRouter>
        <BriefBar />
      </MemoryRouter>,
    );
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.contexts[0]).toBe(screen.getByTestId("brief-running").querySelector("b"));
    frame(0);
    frame(TWEEN_MS);
    expect(screen.getByTestId("brief-running")).toHaveTextContent("5 running");
    expect(screen.getByTestId("brief-awaiting")).toHaveTextContent("2 awaiting input");
  });
});
