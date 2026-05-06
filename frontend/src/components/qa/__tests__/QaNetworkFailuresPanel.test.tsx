import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
}));

// Mock axios so the panel's authenticated fetches go through a stub. The
// real `@/api/client` adds the `/api/v1` prefix and Bearer header — testing
// the call shape here makes sure we wired through axios (not bare fetch).
// `vi.mock` factories are hoisted, so use `vi.hoisted` to share the spy.
const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/api/client", () => ({
  api: { get: apiGet },
}));

import { QaNetworkFailuresPanel } from "../QaNetworkFailuresPanel";
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

function expandFirstEntry() {
  const entry = screen.getByTestId("qa-network-entry") as HTMLDetailsElement;
  entry.open = true;
  // The onToggle handler listens for the bubble-phase 'toggle' event the
  // browser dispatches when <details> opens.
  entry.dispatchEvent(new Event("toggle", { bubbles: false }));
  return entry;
}

describe("QaNetworkFailuresPanel", () => {
  beforeEach(() => {
    mockedUseAppArtifacts.mockReset();
    apiGet.mockReset();
  });

  it("renders the empty-state when no network failures are captured", () => {
    mockedUseAppArtifacts.mockReturnValue(makeQueryResult({ data: [] }));
    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-network-empty")).toHaveTextContent(
      "No network failures captured.",
    );
  });

  it("does NOT fetch any HAR until the user expands a details block", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["fail.har", "other.har", "third.har"] }),
    );
    apiGet.mockResolvedValue({ data: "{}" });
    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    // 3 HARs would have triggered 3 concurrent fetches under the eager
    // pattern — lazy mode means zero until the user expands a block.
    expect(apiGet).not.toHaveBeenCalled();
    // All three are listed as collapsed details so the user can pick.
    expect(screen.getAllByTestId("qa-network-entry")).toHaveLength(3);
  });

  it("fetches lazily on expand and parses HAR-shaped JSON into a table", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["fail.har"] }),
    );
    const har = {
      log: {
        entries: [
          {
            request: { url: "https://x/a", method: "GET" },
            response: { status: 500 },
          },
          {
            request: { url: "https://x/b", method: "POST" },
            response: { status: 503 },
          },
        ],
      },
    };
    apiGet.mockResolvedValue({ data: JSON.stringify(har) });

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    expect(apiGet).not.toHaveBeenCalled();

    expandFirstEntry();

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith(
        "/qa/runs/RUN1/apps/hub/artifacts/network/fail.har",
        expect.objectContaining({ responseType: "text" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText("https://x/a")).toBeInTheDocument();
    });
    expect(screen.getByText("https://x/b")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("503")).toBeInTheDocument();
    expect(screen.getByText("GET")).toBeInTheDocument();
    expect(screen.getByText("POST")).toBeInTheDocument();
  });

  it("falls back to a raw <pre> with a warning when JSON has an unrecognised shape", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["weird.json"] }),
    );
    apiGet.mockResolvedValue({
      data: JSON.stringify({ unknown: "shape" }),
    });

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    expandFirstEntry();
    await waitFor(() => {
      expect(screen.getByText(/Unrecognised JSON shape/)).toBeInTheDocument();
    });
    expect(screen.getByText(/"unknown":/)).toBeInTheDocument();
  });

  it("parses the flat-array shape (failures.json sidecar)", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["failures.json"] }),
    );
    apiGet.mockResolvedValue({
      data: JSON.stringify([
        { url: "https://y/a", method: "GET", status: 502 },
      ]),
    });

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    expandFirstEntry();
    await waitFor(() => {
      expect(screen.getByText("https://y/a")).toBeInTheDocument();
    });
    expect(screen.getByText("502")).toBeInTheDocument();
  });
});
