import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

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

  describe("lightbox", () => {
    function setupSingleScreenshot(filename = "page.png") {
      mockedUseAppArtifacts.mockReturnValue(
        makeQueryResult({ data: [filename] }),
      );
      mockedUseArtifactBlobUrl.mockReturnValue({
        url: `blob:fake/${filename}`,
        loading: false,
        error: null,
      });
    }

    it("opens the lightbox when the thumbnail button is clicked", () => {
      setupSingleScreenshot();
      render(<QaArtifactGrid runId="RUN1" app="hub" />);
      // Lightbox is not in the DOM until the thumb is clicked.
      expect(screen.queryByTestId("qa-artifact-lightbox")).not.toBeInTheDocument();

      fireEvent.click(screen.getByTestId("qa-artifact-thumb-button"));

      const dialog = screen.getByTestId("qa-artifact-lightbox");
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("role", "dialog");
      expect(dialog).toHaveAttribute("aria-modal", "true");
      // Full-size image reuses the same blob URL the thumbnail already fetched —
      // no extra request, no auth issue.
      const fullImg = screen.getByTestId("qa-artifact-lightbox-image");
      expect(fullImg).toHaveAttribute("src", "blob:fake/page.png");
    });

    it("closes the lightbox when Close is clicked", () => {
      setupSingleScreenshot();
      render(<QaArtifactGrid runId="RUN1" app="hub" />);
      fireEvent.click(screen.getByTestId("qa-artifact-thumb-button"));
      expect(screen.getByTestId("qa-artifact-lightbox")).toBeInTheDocument();

      fireEvent.click(screen.getByTestId("qa-artifact-lightbox-close"));
      expect(screen.queryByTestId("qa-artifact-lightbox")).not.toBeInTheDocument();
    });

    it("closes the lightbox when the backdrop is clicked", () => {
      setupSingleScreenshot();
      render(<QaArtifactGrid runId="RUN1" app="hub" />);
      fireEvent.click(screen.getByTestId("qa-artifact-thumb-button"));
      const dialog = screen.getByTestId("qa-artifact-lightbox");
      // Click the dialog's outer backdrop element directly.
      fireEvent.click(dialog);
      expect(screen.queryByTestId("qa-artifact-lightbox")).not.toBeInTheDocument();
    });

    it("does NOT close when the lightbox image itself is clicked (stopPropagation)", () => {
      setupSingleScreenshot();
      render(<QaArtifactGrid runId="RUN1" app="hub" />);
      fireEvent.click(screen.getByTestId("qa-artifact-thumb-button"));
      fireEvent.click(screen.getByTestId("qa-artifact-lightbox-image"));
      // Lightbox stays open — only the backdrop / close button dismiss.
      expect(screen.getByTestId("qa-artifact-lightbox")).toBeInTheDocument();
    });

    it("closes the lightbox when Escape is pressed", () => {
      setupSingleScreenshot();
      render(<QaArtifactGrid runId="RUN1" app="hub" />);
      fireEvent.click(screen.getByTestId("qa-artifact-thumb-button"));
      expect(screen.getByTestId("qa-artifact-lightbox")).toBeInTheDocument();

      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByTestId("qa-artifact-lightbox")).not.toBeInTheDocument();
    });
  });
});
