import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QueueResponse } from "@/lib/types";
import { useAuthStore } from "@/stores/authStore";

// The header reads the /queue poll for the mark's breathing dot; mock the api
// boundary so each test sets how many jobs run.
const mockFetchQueue = vi.fn<() => Promise<QueueResponse>>();
vi.mock("@/api/queue", () => ({
  fetchQueue: () => mockFetchQueue(),
}));

import { TopBar } from "../TopBar";

function queueWith(running: number): QueueResponse {
  return { depth: running, pending: 0, running, awaiting_input: 0, paused: 0 };
}

function renderBar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

/** The mark's cream dot: the second of its two shapes. */
function dotOf(mark: HTMLElement) {
  const dot = mark.querySelectorAll("svg path")[1];
  if (!dot) throw new Error("the mark has no dot");
  return dot;
}

beforeEach(() => {
  useAuthStore.setState({ authMode: null, user: null });
  mockFetchQueue.mockReset();
  mockFetchQueue.mockResolvedValue(queueWith(0));
});

describe("TopBar — the family header", () => {
  it("carries the lockup and the tagline, and no breadcrumb", () => {
    renderBar();
    expect(screen.getByRole("img", { name: "embry0" })).toBeInTheDocument();
    expect(screen.getByText("Software that is alive")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  });

  it("shows the signed-in identity and a sign-out control for an SSO session", () => {
    useAuthStore.setState({
      authMode: "session",
      user: { email: "operator@example.com", name: "Operator", tier: "operator" },
    });
    renderBar();
    expect(screen.getByText("operator@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("says the session runs on an API key when there is no SSO identity", () => {
    useAuthStore.setState({ authMode: "key", user: null });
    renderBar();
    expect(screen.getByText("API key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });
});

describe("TopBar — the mark is alive while a job runs", () => {
  it("breathes the cream dot and says a job is running while one is", async () => {
    mockFetchQueue.mockResolvedValue(queueWith(2));
    renderBar();
    const mark = await screen.findByRole("img", { name: "embry0, a job is running" });
    expect(dotOf(mark).getAttribute("class")).toContain("motion-breathe");
  });

  it("rests the dot once nothing runs", async () => {
    mockFetchQueue.mockResolvedValue(queueWith(1));
    const { client } = renderBar();
    await screen.findByRole("img", { name: "embry0, a job is running" });
    mockFetchQueue.mockResolvedValue(queueWith(0));
    await client.refetchQueries({ queryKey: ["queue"] });
    const mark = await screen.findByRole("img", { name: "embry0" });
    expect(dotOf(mark).getAttribute("class")).not.toContain("motion-breathe");
  });
});
