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

const EMPTY_GIT = { commits: [], repos: [] };

describe("ActivityPage", () => {
  it("renders the activity-band region and a heartbeat indicator", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-band"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("activity-heartbeat")).toBeInTheDocument();
  });

  it("renders an empty state for events when /events returns nothing", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-events-empty"),
    ).toBeInTheDocument();
  });

  it("renders a row per /events payload with event_type + task id, parsing detail", async () => {
    // Real /events shape: numeric id/task_id, event_type, JSON-string detail,
    // created_at timestamp. detail must be parsed, never rendered as an object.
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([
      {
        id: 73,
        task_id: 13,
        event_type: "consensus_review",
        detail: JSON.stringify({ verdict: "pass", note: "looks good" }),
        created_at: new Date(Date.now() - 5_000).toISOString(),
      },
      {
        id: 74,
        task_id: 14,
        event_type: "task_done",
        detail: "{}",
        created_at: new Date(Date.now() - 60_000).toISOString(),
      },
    ]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    const rowOne = await screen.findByTestId("activity-event-73");
    expect(rowOne).toHaveTextContent("consensus_review");
    expect(rowOne).toHaveTextContent("13");
    // The parsed verdict surfaces; the raw JSON object must NOT.
    expect(rowOne).toHaveTextContent("pass");
    expect(rowOne).not.toHaveTextContent("[object Object]");
    expect(screen.getByTestId("activity-event-74")).toHaveTextContent(
      "task_done",
    );
  });

  it("does not crash when an event detail is not valid JSON", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([
      {
        id: 99,
        task_id: 1,
        event_type: "raw_note",
        detail: "not json at all",
        created_at: new Date().toISOString(),
      },
    ]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    const row = await screen.findByTestId("activity-event-99");
    expect(row).toHaveTextContent("raw_note");
    expect(row).toHaveTextContent("not json at all");
  });

  it("renders an empty state for git-activity when /git-activity returns no repos", async () => {
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    expect(
      await screen.findByTestId("activity-git-empty"),
    ).toBeInTheDocument();
  });

  it("renders a row per /git-activity repo with name + branch + open issues", async () => {
    // Real /git-activity shape: { commits, repos } where repos carry GitHub
    // metadata. The page must iterate repos, never render the wrapper object.
    vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    vi.spyOn(agent, "fetchGitActivity").mockResolvedValue({
      commits: [],
      repos: [
        {
          name: "embry0",
          pushedAt: new Date(Date.now() - 10_000).toISOString(),
          openIssues: 3,
          defaultBranch: "main",
          url: "https://github.com/acme-org/embry0",
        },
        {
          name: "tooling",
          pushedAt: new Date(Date.now() - 600_000).toISOString(),
          openIssues: 0,
          defaultBranch: "master",
          url: "https://github.com/acme-org/tooling",
        },
      ],
    });

    renderWithProviders();

    const rowOne = await screen.findByTestId("activity-git-embry0");
    expect(rowOne).toHaveTextContent("embry0");
    expect(rowOne).toHaveTextContent("main");
    expect(rowOne).toHaveTextContent("3 open");

    const rowTwo = screen.getByTestId("activity-git-tooling");
    expect(rowTwo).toHaveTextContent("tooling");
    expect(rowTwo).toHaveTextContent("master");
  });

  it("polls both /events and /git-activity at the standard agent refetch cadence", async () => {
    const events = vi.spyOn(agent, "fetchEvents").mockResolvedValue([]);
    const git = vi.spyOn(agent, "fetchGitActivity").mockResolvedValue(EMPTY_GIT);

    renderWithProviders();

    // First-paint fetch — proves the page is wired to both endpoints, not just one.
    await screen.findByTestId("activity-band");
    expect(events).toHaveBeenCalledTimes(1);
    expect(git).toHaveBeenCalledTimes(1);
  });
});
