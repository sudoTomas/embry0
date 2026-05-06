import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Stub useFlake at the hook module so the view doesn't need a
// QueryClientProvider — same pattern as CacheAnalyticsView.test.tsx.
const mockUseFlake = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useFlake: (repo: string | undefined, windowDays?: number) =>
    mockUseFlake(repo, windowDays),
}));

import { FlakeHeatmap } from "../FlakeHeatmap";
import type { FlakeResponse } from "@/lib/types";

const FULL: FlakeResponse = {
  repo: "org/r1",
  window_days: 7,
  apps: [
    {
      app_name: "hub",
      total_runs: 4,
      flake_count: 2,
      flake_score: 0.5,
      daily: [
        { date: "2026-04-30", flakes: 0 },
        { date: "2026-05-01", flakes: 0 },
        { date: "2026-05-02", flakes: 1 },
        { date: "2026-05-03", flakes: 0 },
        { date: "2026-05-04", flakes: 0 },
        { date: "2026-05-05", flakes: 1 },
        { date: "2026-05-06", flakes: 0 },
      ],
    },
    {
      app_name: "companion",
      total_runs: 3,
      flake_count: 0,
      flake_score: 0.0,
      daily: [
        { date: "2026-04-30", flakes: 0 },
        { date: "2026-05-01", flakes: 0 },
        { date: "2026-05-02", flakes: 0 },
        { date: "2026-05-03", flakes: 0 },
        { date: "2026-05-04", flakes: 0 },
        { date: "2026-05-05", flakes: 0 },
        { date: "2026-05-06", flakes: 0 },
      ],
    },
  ],
};

beforeEach(() => {
  mockUseFlake.mockReset();
});

describe("FlakeHeatmap", () => {
  it("renders header summary, day-key header, and one row per app", () => {
    mockUseFlake.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<FlakeHeatmap repo="org/r1" />);

    // Header surfaces repo + total flakes + window length.
    expect(screen.getByText("org/r1")).toBeInTheDocument();
    expect(
      screen.getByText(/2 apps · 2 total flakes · last 7 days/i),
    ).toBeInTheDocument();

    // One row per app, in the order returned by the backend (which is
    // already flake_score desc — hub first, companion second).
    expect(screen.getByTestId("flake-row-hub")).toBeInTheDocument();
    expect(screen.getByTestId("flake-row-companion")).toBeInTheDocument();

    // Each row has 7 cells (one per day in the 7-day window).
    const hubCells = screen
      .getAllByTestId(/^flake-cell-hub-/)
      .map((el) => el.getAttribute("data-flakes"));
    expect(hubCells).toEqual(["0", "0", "1", "0", "0", "1", "0"]);

    const companionCells = screen
      .getAllByTestId(/^flake-cell-companion-/)
      .map((el) => el.getAttribute("data-flakes"));
    expect(companionCells).toEqual(["0", "0", "0", "0", "0", "0", "0"]);
  });

  it("renders zero cells differently from flake cells", () => {
    mockUseFlake.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" />);

    // Pull two cells from the hub row: one with flakes=0 and one with
    // flakes=1 — their classes should differ (slate vs rose).
    const zeroCell = screen.getByTestId("flake-cell-hub-2026-04-30");
    const flakeCell = screen.getByTestId("flake-cell-hub-2026-05-02");
    expect(zeroCell.className).not.toEqual(flakeCell.className);
    // Sanity: the zero cell uses the slate-y bg, the flake cell uses rose.
    expect(zeroCell.className).toContain("bg-white/5");
    expect(flakeCell.className).toContain("bg-rose-500/30");
  });

  it("emits a tooltip with app + date + flake count for each cell", () => {
    mockUseFlake.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" />);

    const flakeCell = screen.getByTestId("flake-cell-hub-2026-05-02");
    expect(flakeCell.getAttribute("title")).toBe(
      "hub on 2026-05-02: 1 flake",
    );
    const zeroCell = screen.getByTestId("flake-cell-hub-2026-04-30");
    expect(zeroCell.getAttribute("title")).toBe(
      "hub on 2026-04-30: 0 flakes",
    );
  });

  it("renders the empty-state copy when apps is empty", () => {
    mockUseFlake.mockReturnValue({
      data: { repo: "org/empty", window_days: 7, apps: [] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/empty" />);

    expect(screen.getByTestId("flake-heatmap-empty")).toBeInTheDocument();
    expect(
      screen.getByText(/no qa runs in the last 7 days/i),
    ).toBeInTheDocument();
    // Header should still report 0 apps + 0 flakes.
    expect(
      screen.getByText(/0 apps · 0 total flakes · last 7 days/i),
    ).toBeInTheDocument();
  });

  it("renders the loading skeleton while data is unresolved", () => {
    mockUseFlake.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const { container } = render(<FlakeHeatmap repo="org/r1" />);
    expect(
      container.querySelector('[data-testid="flake-heatmap"]'),
    ).toBeNull();
  });

  it("shows a generic error when the load fails", () => {
    mockUseFlake.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { response: { status: 500 } },
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" />);
    expect(
      screen.getByText(/Failed to load flake heatmap/i),
    ).toBeInTheDocument();
  });

  it("forwards the windowDays prop to the hook", () => {
    mockUseFlake.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" windowDays={30} />);
    expect(mockUseFlake).toHaveBeenCalledWith("org/r1", 30);
  });

  it("renders the numeric flake count INSIDE high-flake cells (a11y)", () => {
    // Buckets >= 3 must show a non-color signal (the numeral) so color-
    // blind users can read intensity without relying on opacity alone.
    // Cells with 0/1/2 flakes stay glyph-free to keep the grid scannable.
    const data: FlakeResponse = {
      repo: "org/r1",
      window_days: 7,
      apps: [
        {
          app_name: "hub",
          total_runs: 10,
          flake_count: 8,
          flake_score: 0.8,
          daily: [
            { date: "2026-04-30", flakes: 0 },
            { date: "2026-05-01", flakes: 1 },
            { date: "2026-05-02", flakes: 2 },
            { date: "2026-05-03", flakes: 3 },
            { date: "2026-05-04", flakes: 5 },
            { date: "2026-05-05", flakes: 0 },
            { date: "2026-05-06", flakes: 0 },
          ],
        },
      ],
    };
    mockUseFlake.mockReturnValue({
      data,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" />);

    // Cell with 5 flakes renders the numeral inside (non-color signal).
    const heavyCell = screen.getByTestId("flake-cell-hub-2026-05-04");
    expect(heavyCell.textContent).toBe("5");

    // Cell with 3 flakes renders the numeral (boundary case >=3).
    const threeCell = screen.getByTestId("flake-cell-hub-2026-05-03");
    expect(threeCell.textContent).toBe("3");

    // Cell with 2 flakes (below the threshold) does NOT render a numeral.
    const lightCell = screen.getByTestId("flake-cell-hub-2026-05-02");
    expect(lightCell.textContent).toBe("");

    // Cell with 0 flakes does NOT render a numeral.
    const zeroCell = screen.getByTestId("flake-cell-hub-2026-04-30");
    expect(zeroCell.textContent).toBe("");
  });

  it("emits aria-label on every cell so screen readers can announce data", () => {
    mockUseFlake.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<FlakeHeatmap repo="org/r1" />);

    // aria-label mirrors the title so screen readers (which often ignore
    // title on non-interactive divs) still announce the data point.
    const flakeCell = screen.getByTestId("flake-cell-hub-2026-05-02");
    expect(flakeCell.getAttribute("aria-label")).toBe(
      "hub on 2026-05-02: 1 flake",
    );
    const zeroCell = screen.getByTestId("flake-cell-hub-2026-04-30");
    expect(zeroCell.getAttribute("aria-label")).toBe(
      "hub on 2026-04-30: 0 flakes",
    );
    // Cells declare gridcell role so AT exposes the heatmap grid structure.
    expect(flakeCell.getAttribute("role")).toBe("gridcell");
  });
});
