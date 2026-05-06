import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock both hooks BEFORE importing the component so the import sees the mocks.
vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
}));
vi.mock("@/hooks/useArtifactBlobUrl", () => ({
  useArtifactBlobUrl: vi.fn(),
}));

import { QaArtifactGrid } from "../QaArtifactGrid";
import { useAppArtifacts } from "@/hooks/useQaDashboard";
import { useArtifactBlobUrl } from "@/hooks/useArtifactBlobUrl";

const mockedUseAppArtifacts = vi.mocked(useAppArtifacts);
const mockedUseArtifactBlobUrl = vi.mocked(useArtifactBlobUrl);

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
    mockedUseArtifactBlobUrl.mockReset();
  });

  it("renders one thumbnail per filename, with src wired to the hook's blob URL", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["a.png", "b.png"] }),
    );
    // Return a deterministic blob URL keyed off the filename so we can assert
    // the right hook output flows into the right `<img>`.
    mockedUseArtifactBlobUrl.mockImplementation(
      (_runId, _app, _kind, filename) => ({
        url: `blob:fake/${filename}`,
        loading: false,
        error: null,
      }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);

    const grid = screen.getByTestId("qa-artifact-grid");
    expect(grid).toBeInTheDocument();
    // Wrapper <a target="_blank"> was dropped — it would not carry the Bearer
    // header on a top-level navigation, so the inline thumbnail is sufficient.
    expect(grid.querySelectorAll("a")).toHaveLength(0);

    const imgs = grid.querySelectorAll("img");
    expect(imgs).toHaveLength(2);
    expect(imgs[0].getAttribute("src")).toBe("blob:fake/a.png");
    expect(imgs[0].getAttribute("alt")).toBe("a.png");
    expect(imgs[1].getAttribute("src")).toBe("blob:fake/b.png");

    // The hook was called with the auth-passthrough coordinates the panel
    // resolved from the listing.
    expect(mockedUseArtifactBlobUrl).toHaveBeenCalledWith(
      "RUN1",
      "hub",
      "screenshots",
      "a.png",
    );
    expect(mockedUseArtifactBlobUrl).toHaveBeenCalledWith(
      "RUN1",
      "hub",
      "screenshots",
      "b.png",
    );
  });

  it("renders a per-thumb loading placeholder while the blob URL is being fetched", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["pending.png"] }),
    );
    mockedUseArtifactBlobUrl.mockReturnValue({
      url: null,
      loading: true,
      error: null,
    });
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-thumb-loading")).toHaveAttribute(
      "data-filename",
      "pending.png",
    );
  });

  it("renders a per-thumb error placeholder when the blob fetch fails", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["denied.png"] }),
    );
    mockedUseArtifactBlobUrl.mockReturnValue({
      url: null,
      loading: false,
      error: "Request failed with status code 401",
    });
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-thumb-error")).toHaveAttribute(
      "data-filename",
      "denied.png",
    );
  });

  it("renders the empty-state when no screenshots exist", () => {
    mockedUseAppArtifacts.mockReturnValue(makeQueryResult({ data: [] }));
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-grid-empty")).toHaveTextContent(
      "No screenshots captured.",
    );
  });

  it("renders the loading state while the listing query is pending", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ isLoading: true, data: undefined }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(
      screen.getByTestId("qa-artifact-grid-loading"),
    ).toBeInTheDocument();
  });

  it("renders the error state when the listing query fails", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ isError: true, data: undefined }),
    );
    render(<QaArtifactGrid runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-artifact-grid-error")).toBeInTheDocument();
  });
});
