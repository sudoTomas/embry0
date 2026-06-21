import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AgentTask, AgentTaskBlockedBy } from "@/api/agent";

// Mock the agent module so the page renders without hitting axios. Each
// reference is named so the assertions below can pin the URL/method shape
// the page actually emits — the boundary mirrors the api/agent.test.ts
// contract pinning.
const mockFetchTasks = vi.fn<() => Promise<AgentTask[]>>();
const mockFetchTaskBlockedBy =
  vi.fn<(id: string) => Promise<AgentTaskBlockedBy>>();
const mockDeployTask = vi.fn<(id: string) => Promise<AgentTask>>();
const mockRequeueTask = vi.fn<(id: string) => Promise<AgentTask>>();
const mockRetryTask = vi.fn<(id: string) => Promise<AgentTask>>();
const mockStopTask = vi.fn<(id: string) => Promise<AgentTask>>();
const mockDeadLetterTask = vi.fn<(id: string) => Promise<AgentTask>>();

vi.mock("@/api/agent", () => ({
  fetchTasks: () => mockFetchTasks(),
  fetchTaskBlockedBy: (id: string) => mockFetchTaskBlockedBy(id),
  deployTask: (id: string) => mockDeployTask(id),
  requeueTask: (id: string) => mockRequeueTask(id),
  retryTask: (id: string) => mockRetryTask(id),
  stopTask: (id: string) => mockStopTask(id),
  deadLetterTask: (id: string) => mockDeadLetterTask(id),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { TasksPage } from "../TasksPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <TasksPage />
      </QueryClientProvider>,
    ),
  };
}

const TASK_A: AgentTask = { id: "a", status: "running", title: "Task A" };
const TASK_B: AgentTask = { id: "b", status: "queued", title: "Task B" };

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchTasks.mockResolvedValue([TASK_A, TASK_B]);
  mockFetchTaskBlockedBy.mockResolvedValue({ id: "a", blocked_by: [] });
});

describe("TasksPage — list rendering", () => {
  it("renders one row per task with id + status", async () => {
    renderPage();
    expect(await screen.findByTestId("task-row-a")).toHaveTextContent(/Task A/);
    expect(screen.getByTestId("task-row-a")).toHaveTextContent(/running/i);
    expect(screen.getByTestId("task-row-b")).toHaveTextContent(/Task B/);
  });

  it("renders empty state when no tasks", async () => {
    mockFetchTasks.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText(/no tasks/i)).toBeInTheDocument();
  });

  it("renders tasks with numeric ids and a 'stopped' status (real /stats shape)", async () => {
    mockFetchTasks.mockResolvedValueOnce([
      { id: 13, status: "done", title: "Done task", cost_usd: 1.01 },
      { id: 14, status: "stopped", title: "Stopped task" },
    ]);
    renderPage();
    const done = await screen.findByTestId("task-row-13");
    expect(done).toHaveTextContent("Done task");
    expect(done).toHaveTextContent("13");
    const stopped = screen.getByTestId("task-row-14");
    expect(stopped).toHaveTextContent(/stopped/i);
    expect(stopped).not.toHaveTextContent("[object Object]");
  });
});

describe("TasksPage — row actions", () => {
  const actions = [
    ["deploy", () => mockDeployTask] as const,
    ["requeue", () => mockRequeueTask] as const,
    ["retry", () => mockRetryTask] as const,
    ["stop", () => mockStopTask] as const,
    ["dead-letter", () => mockDeadLetterTask] as const,
  ];

  for (const [action, mockGetter] of actions) {
    it(`'${action}' button calls the matching mutation with the row's id`, async () => {
      const m = mockGetter();
      m.mockResolvedValueOnce({ ...TASK_A });
      renderPage();
      const button = await screen.findByLabelText(`${action} a`);
      fireEvent.click(button);
      await waitFor(() => expect(m).toHaveBeenCalledWith("a"));
    });
  }

  it("successful action invalidates BOTH the tasks list AND the selected task's blocked-by query", async () => {
    mockDeployTask.mockResolvedValueOnce({ ...TASK_A });
    const { client } = renderPage();
    // Click the row first to select task 'a' — this is what makes the
    // blocked-by query a live cache entry. Then deploy.
    fireEvent.click(await screen.findByTestId("task-row-a"));
    await waitFor(() =>
      expect(mockFetchTaskBlockedBy).toHaveBeenCalledWith("a"),
    );

    const invalidate = vi.spyOn(client, "invalidateQueries");
    fireEvent.click(screen.getByLabelText("deploy a"));

    await waitFor(() => expect(mockDeployTask).toHaveBeenCalled());
    // Both query keys must be invalidated — the assay caught a prior pass
    // invalidating only ["agent","tasks"] and leaving the blocked-by graph
    // visually stale.
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["agent", "tasks"] }),
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["agent", "tasks", "a", "blocked-by"],
    });
  });
});

describe("TasksPage — dependency graph", () => {
  it("clicking a row selects it and triggers a blocked-by fetch", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("task-row-a"));
    await waitFor(() =>
      expect(mockFetchTaskBlockedBy).toHaveBeenCalledWith("a"),
    );
  });

  it("renders the dependency-graph surface for the selected task", async () => {
    mockFetchTaskBlockedBy.mockResolvedValue({
      id: "a",
      blocked_by: [{ id: "x", status: "running", title: "Upstream X" }],
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("task-row-a"));
    expect(await screen.findByTestId("blocked-by-graph")).toBeInTheDocument();
  });

  it("hides the dependency-graph surface when no row is selected", async () => {
    renderPage();
    await screen.findByTestId("task-row-a");
    expect(screen.queryByTestId("blocked-by-graph")).toBeNull();
  });
});
