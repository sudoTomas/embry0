import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
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

describe("QaNetworkFailuresPanel", () => {
  beforeEach(() => {
    mockedUseAppArtifacts.mockReset();
    vi.unstubAllGlobals();
  });

  it("renders the empty-state when no network failures are captured", () => {
    mockedUseAppArtifacts.mockReturnValue(makeQueryResult({ data: [] }));
    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-network-empty")).toHaveTextContent(
      "No network failures captured.",
    );
  });

  it("parses HAR-shaped JSON and renders a method/url/status table", async () => {
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
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(har),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/qa/runs/RUN1/apps/hub/artifacts/network/fail.har",
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
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ unknown: "shape" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    await waitFor(() => {
      expect(screen.getByText(/Unrecognised JSON shape/)).toBeInTheDocument();
    });
    expect(screen.getByText(/"unknown":/)).toBeInTheDocument();
  });

  it("parses the flat-array shape (failures.json sidecar)", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["failures.json"] }),
    );
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        JSON.stringify([
          { url: "https://y/a", method: "GET", status: 502 },
        ]),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<QaNetworkFailuresPanel runId="RUN1" app="hub" />);
    await waitFor(() => {
      expect(screen.getByText("https://y/a")).toBeInTheDocument();
    });
    expect(screen.getByText("502")).toBeInTheDocument();
  });
});
