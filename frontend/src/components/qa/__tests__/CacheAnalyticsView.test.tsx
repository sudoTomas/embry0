import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Stub useCacheAnalytics at the hook module so the view doesn't need a
// QueryClientProvider — same pattern as AffectedSetView.test.tsx.
const mockUseCacheAnalytics = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useCacheAnalytics: (repo: string | undefined, windowDays?: number) =>
    mockUseCacheAnalytics(repo, windowDays),
}));

import { CacheAnalyticsView } from "../CacheAnalyticsView";
import type { CacheAnalyticsResponse } from "@/lib/types";

const FULL: CacheAnalyticsResponse = {
  repo: "org/r1",
  window_days: 30,
  total_runs: 12,
  total_subtasks: 36,
  layers: [
    { layer: "prebaked_image", hits: 30, misses: 6, hit_ratio: 30 / 36 },
    { layer: "shared_volume", hits: 18, misses: 18, hit_ratio: 0.5 },
    { layer: "turbo_remote", hits: 80, misses: 40, hit_ratio: 80 / 120 },
  ],
  cold_cache_apps: ["legacy-app"],
};

beforeEach(() => {
  mockUseCacheAnalytics.mockReset();
});

describe("CacheAnalyticsView", () => {
  it("renders header, three layer bars, and the cold-cache list", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CacheAnalyticsView repo="org/r1" />);

    // Header surfaces repo + run/sub-task counters.
    expect(screen.getByText("org/r1")).toBeInTheDocument();
    expect(
      screen.getByText(/last 30 days · 12 runs · 36 sub-tasks/i),
    ).toBeInTheDocument();

    // Three layer bars are rendered with correct labels.
    const pi = screen.getByTestId("cache-layer-prebaked_image");
    expect(pi).toHaveTextContent("Prebaked image");
    expect(pi).toHaveTextContent("30/36 (83%)");

    const sv = screen.getByTestId("cache-layer-shared_volume");
    expect(sv).toHaveTextContent("Shared volume");
    expect(sv).toHaveTextContent("18/36 (50%)");

    const tr = screen.getByTestId("cache-layer-turbo_remote");
    expect(tr).toHaveTextContent("Turbo remote");
    expect(tr).toHaveTextContent("80/120 (67%)");

    // Cold-cache list renders the offender app.
    const cold = screen.getByTestId("cold-cache-section");
    expect(cold).toHaveTextContent(/Apps with low hit rates \(1\)/i);
    expect(cold).toHaveTextContent("legacy-app");

    // The header carries a tooltip disclosing the cold-app threshold so
    // viewers can inspect the rule without reading source.
    const heading = cold.querySelector("h2");
    expect(heading).not.toBeNull();
    expect(heading).toHaveAttribute(
      "title",
      "hit ratio below 25% over 3+ sub-tasks",
    );
  });

  it("renders the empty-cold-cache state when cold_cache_apps is empty", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: { ...FULL, cold_cache_apps: [] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CacheAnalyticsView repo="org/r1" />);

    const cold = screen.getByTestId("cold-cache-section");
    expect(cold).toHaveTextContent(/Apps with low hit rates \(0\)/i);
    expect(cold).toHaveTextContent(/None\./);
  });

  it("renders zeroed bars and zero counters on an empty repo", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: {
        repo: "org/empty",
        window_days: 30,
        total_runs: 0,
        total_subtasks: 0,
        layers: [
          { layer: "prebaked_image", hits: 0, misses: 0, hit_ratio: 0 },
          { layer: "shared_volume", hits: 0, misses: 0, hit_ratio: 0 },
          { layer: "turbo_remote", hits: 0, misses: 0, hit_ratio: 0 },
        ],
        cold_cache_apps: [],
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CacheAnalyticsView repo="org/empty" />);

    expect(
      screen.getByText(/last 30 days · 0 runs · 0 sub-tasks/i),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("cache-layer-prebaked_image"),
    ).toHaveTextContent("0/0 (0%)");
  });

  it("renders the loading skeleton while data is unresolved", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const { container } = render(<CacheAnalyticsView repo="org/r1" />);
    // No content section should render yet.
    expect(
      container.querySelector('[data-testid="cache-analytics-view"]'),
    ).toBeNull();
  });

  it("shows a generic error when the load fails", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { response: { status: 500 } },
      refetch: vi.fn(),
    });
    render(<CacheAnalyticsView repo="org/r1" />);
    expect(
      screen.getByText(/Failed to load cache analytics/i),
    ).toBeInTheDocument();
  });

  it("forwards the windowDays prop to the hook", () => {
    mockUseCacheAnalytics.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<CacheAnalyticsView repo="org/r1" windowDays={7} />);
    expect(mockUseCacheAnalytics).toHaveBeenCalledWith("org/r1", 7);
  });
});
