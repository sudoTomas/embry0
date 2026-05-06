import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock the hook BEFORE importing the component so the import sees the mock.
vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
}));

import { QaArtifactGrid } from "../QaArtifactGrid";
import { useAppArtifacts } from "@/hooks/useQaDashboard";

const mockedUseAppArtifacts = vi.mocked(useAppArtifacts);

function makeQueryResult<T>(over: Partial<{
  data: T;
  isLoading: boolean;
  isError: boolean;
}> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    ...over,
  } as ReturnType<typeof useAppArtifacts>;
}

describe("QaArtifactGrid", () => {
  beforeEach(() => {
    mockedUseAppArtifacts.mockReset();
  });

  it("renders a thumbnail per filename with auth-passthrough URLs", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["a.png", "b.png"] }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);

    const grid = screen.getByTestId("qa-artifact-grid");
    expect(grid).toBeInTheDocument();
    const links = grid.querySelectorAll("a");
    expect(links).toHaveLength(2);
    // Both <img> and the wrapping <a> use the artifact passthrough URL.
    expect(links[0].getAttribute("href")).toBe(
      "/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/a.png",
    );
    const imgs = grid.querySelectorAll("img");
    expect(imgs[0].getAttribute("src")).toBe(
      "/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/a.png",
    );
    expect(imgs[0].getAttribute("alt")).toBe("a.png");
  });

  it("renders the empty-state when no screenshots exist", () => {
    mockedUseAppArtifacts.mockReturnValue(makeQueryResult({ data: [] }));
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-grid-empty")).toHaveTextContent(
      "No screenshots captured.",
    );
  });

  it("renders the loading state while the query is pending", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ isLoading: true, data: undefined }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(
      screen.getByTestId("qa-artifact-grid-loading"),
    ).toBeInTheDocument();
  });

  it("renders the error state when the query fails", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ isError: true, data: undefined }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-grid-error")).toBeInTheDocument();
  });
});
