import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QueueResponse } from "@/lib/types";

vi.mock("@/stores/layoutStore", () => ({
  useLayoutStore: (selector?: (s: { sidebarOpen: boolean }) => unknown) =>
    selector ? selector({ sidebarOpen: true }) : { sidebarOpen: true },
}));

// The sidebar polls /queue for the Console awaiting-input badge — mock the
// api boundary so tests control the count without hitting axios.
const mockFetchQueue = vi.fn<() => Promise<QueueResponse>>();
vi.mock("@/api/queue", () => ({
  fetchQueue: () => mockFetchQueue(),
}));

import { Sidebar } from "../Sidebar";

function renderSidebar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchQueue.mockResolvedValue({
    depth: 0,
    pending: 0,
    running: 0,
    awaiting_input: 0,
    paused: 0,
  });
  document.title = "embry0 — Software that is alive";
});

describe("Sidebar — unified IA (ticket 011)", () => {
  it("renders the six section labels in order: Overview, Work, Pipelines & QA, Infra, Insights, Settings", () => {
    renderSidebar();
    const labels = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent?.trim());
    expect(labels).toEqual([
      "Overview",
      "Work",
      "Pipelines & QA",
      "Infra",
      "Insights",
      "Settings",
    ]);
  });

  it("Work section contains Console, Issues, Jobs, Tasks, Proposals in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Work" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Console",
      "Issues",
      "Jobs",
      "Tasks",
      "Proposals",
    ]);
  });

  it("Infra section contains Sandboxes, Agents, Environments, Repos in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Sandboxes",
      "Agents",
      "Environments",
      "Repos",
    ]);
  });

  it("Pipelines & QA section contains Pipelines, QA, Provider overrides", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Pipelines & QA" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Pipelines",
      "QA",
      "Provider overrides",
    ]);
  });

  it("Overview section contains a single Overview link to /", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Overview" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/");
    expect(links[0].textContent?.trim()).toBe("Overview");
  });

  it("Insights section contains a single Insights link to /insights", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Insights" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/insights");
  });

  it("every nav item points to its expected route (blocker-ticket coverage check)", () => {
    renderSidebar();
    const expectedHrefs: Record<string, string> = {
      Overview: "/",
      Console: "/console",
      Issues: "/issues",
      Jobs: "/jobs",
      Tasks: "/tasks",
      Proposals: "/proposals",
      Pipelines: "/pipelines",
      QA: "/qa/repos",
      "Provider overrides": "/qa/admin/providers",
      Sandboxes: "/sandboxes",
      Agents: "/agents",
      Environments: "/environments",
      Repos: "/repos",
      Insights: "/insights",
      Settings: "/settings",
    };
    for (const [label, href] of Object.entries(expectedHrefs)) {
      const link = screen.getByRole("link", { name: label });
      expect(link, `nav link "${label}"`).toHaveAttribute("href", href);
    }
  });
});

describe("Sidebar — Console awaiting-input prominence", () => {
  it("badges the Console entry with the awaiting_input + paused count", async () => {
    mockFetchQueue.mockResolvedValue({
      depth: 3,
      pending: 1,
      running: 0,
      awaiting_input: 1,
      paused: 1,
    });
    renderSidebar();
    const badge = await screen.findByTestId("nav-awaiting-badge");
    expect(badge).toHaveTextContent("2");
    expect(screen.getByRole("link", { name: /Console/ })).toContainElement(badge);
  });

  it("hides the badge at zero", async () => {
    renderSidebar();
    // Let the queue query settle before asserting absence.
    await waitFor(() => expect(mockFetchQueue).toHaveBeenCalled());
    expect(screen.queryByTestId("nav-awaiting-badge")).toBeNull();
  });

  it('mirrors the count into document.title as "(2⚠) …" and clears it at zero', async () => {
    mockFetchQueue.mockResolvedValue({
      depth: 2,
      pending: 0,
      running: 0,
      awaiting_input: 2,
      paused: 0,
    });
    const { unmount } = renderSidebar();
    await waitFor(() =>
      expect(document.title).toBe("(2⚠) embry0 — Software that is alive"),
    );
    // Unmount restores the unbadged title.
    unmount();
    expect(document.title).toBe("embry0 — Software that is alive");
  });
});
