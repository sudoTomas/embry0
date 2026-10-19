import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockUseWatcherRuns = vi.fn();
const mockTriggerWatcherRun = vi.fn();

vi.mock("@/hooks/useWatcherRuns", () => ({
  useWatcherRuns: () => mockUseWatcherRuns(),
}));
vi.mock("@/api/watcher", () => ({
  triggerWatcherRun: () => mockTriggerWatcherRun(),
}));

import { WatcherPage } from "../WatcherPage";

const RUN = {
  id: "wr-1",
  window_start: new Date().toISOString(),
  window_end: new Date().toISOString(),
  log_lines: 120,
  action: "proposed",
  detail: "crash loop in gateway",
  job_id: "job-abc123def456",
  fingerprint: "fp-1",
  repo: "acme/backend",
  tracker: "linear",
  tracker_key: "RAV-999",
  tracker_url: "https://linear.app/acme/issue/RAV-999",
  created_at: new Date().toISOString(),
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WatcherPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseWatcherRuns.mockReturnValue(ready([RUN]));
});

describe("WatcherPage", () => {
  it("renders the tick trail with action, job link, and Linear proposal link", () => {
    renderPage();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Proposed")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "job-abc123de" })).toHaveAttribute(
      "href",
      "/jobs/job-abc123def456",
    );
    expect(within(table).getByRole("link", { name: "RAV-999" })).toHaveAttribute(
      "href",
      "https://linear.app/acme/issue/RAV-999",
    );
  });

  it("labels a withheld analysis so it does not read as a raw action id", () => {
    mockUseWatcherRuns.mockReturnValue(
      ready([{ ...RUN, id: "wr-2", action: "skipped_unparseable", tracker_key: null, tracker_url: null }]),
    );
    renderPage();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Analysis withheld")).toBeInTheDocument();
    expect(within(table).queryByText(/skipped.unparseable/i)).not.toBeInTheDocument();
  });

  it("runs a tick on demand and refreshes the trail", async () => {
    mockTriggerWatcherRun.mockResolvedValue({ action: "skipped_quiet", log_lines: 3 });
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /run tick now/i }));
    await waitFor(() => expect(mockTriggerWatcherRun).toHaveBeenCalledOnce());
  });

  it("shows an honest empty state when no ticks exist", () => {
    mockUseWatcherRuns.mockReturnValue(ready([]));
    renderPage();
    expect(screen.getByText("No watcher ticks recorded yet")).toBeInTheDocument();
  });
});
