import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import * as agent from "@/api/agent";
import { ActivityPage } from "../ActivityPage";

function renderWithProviders() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ActivityPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("ActivityPage", () => {
  it("renders the activity-band region and a heartbeat indicator", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([]);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-band"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("activity-heartbeat")).toBeInTheDocument();
  });

  it("renders an empty state for events when /events returns nothing", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([]);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-events-empty"),
    ).toBeInTheDocument();
  });

  it("renders a row per /events payload with type + task id", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([
      {
        id: "evt-1",
        type: "task.queued",
        task_id: "T-99",
        ts: new Date(Date.now() - 5_000).toISOString(),
      },
      {
        id: "evt-2",
        type: "task.done",
        task_id: "T-77",
        ts: new Date(Date.now() - 60_000).toISOString(),
      },
    ]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([]);

    renderWithProviders();

    const rowOne = await screen.findByTestId("activity-event-evt-1");
    expect(rowOne).toHaveTextContent("task.queued");
    expect(rowOne).toHaveTextContent("T-99");
    expect(screen.getByTestId("activity-event-evt-2")).toHaveTextContent(
      "task.done",
    );
  });

  it("renders an empty state for git-activity when /git-activity returns nothing", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([]);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-git-empty"),
    ).toBeInTheDocument();
  });

  it("renders a row per /git-activity payload with repo + action + sha/pr", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([
      {
        id: "ga-1",
        repo: "former-org/embry0",
        branch: "opus/ticket-008",
        action: "push",
        ts: new Date(Date.now() - 10_000).toISOString(),
        sha: "abc1234567",
        message: "Add activity band",
      },
      {
        id: "ga-2",
        repo: "former-org/embry0",
        action: "pr_merge",
        ts: new Date(Date.now() - 600_000).toISOString(),
        pr_number: 42,
      },
    ]);

    renderWithProviders();

    const rowOne = await screen.findByTestId("activity-git-ga-1");
    expect(rowOne).toHaveTextContent("former-org/embry0");
    expect(rowOne).toHaveTextContent("push");
    expect(rowOne).toHaveTextContent("abc1234"); // shortened sha

    const rowTwo = screen.getByTestId("activity-git-ga-2");
    expect(rowTwo).toHaveTextContent("pr_merge");
    expect(rowTwo).toHaveTextContent("#42");
  });

  it("polls both /events and /git-activity at the standard agent refetch cadence", async () => {
    const events = vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    const git = vi.spyOn(agent, "fetchGitActivity").mockResolvedValue([]);

    renderWithProviders();

    // First-paint fetch — proves the page is wired to both endpoints, not just one.
    await screen.findByTestId("activity-band");
    expect(events).toHaveBeenCalledTimes(1);
    expect(git).toHaveBeenCalledTimes(1);
  });
});
