import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
}));

import { QaConsoleLogPanel } from "../QaConsoleLogPanel";
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

describe("QaConsoleLogPanel", () => {
  beforeEach(() => {
    mockedUseAppArtifacts.mockReset();
    vi.unstubAllGlobals();
  });

  it("renders the empty-state when no console logs are captured", () => {
    mockedUseAppArtifacts.mockReturnValue(makeQueryResult({ data: [] }));
    render(<QaConsoleLogPanel runId="RUN1" app="hub" />);
    expect(screen.getByTestId("qa-console-log-empty")).toHaveTextContent(
      "No console logs captured.",
    );
  });

  it("lists files as collapsed details when present", () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["browser.log", "page2.log"] }),
    );
    render(<QaConsoleLogPanel runId="RUN1" app="hub" />);
    const entries = screen.getAllByTestId("qa-console-log-entry");
    expect(entries).toHaveLength(2);
    expect(entries[0].getAttribute("data-filename")).toBe("browser.log");
    // The summary text shows the filename.
    expect(screen.getByText("browser.log")).toBeInTheDocument();
  });

  it("fetches the body lazily when the user expands a details block", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["browser.log"] }),
    );
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => "ERROR foo\nERROR bar\n",
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<QaConsoleLogPanel runId="RUN1" app="hub" />);
    // No fetch yet — the details block is still closed.
    expect(fetchMock).not.toHaveBeenCalled();

    const entry = screen.getByTestId("qa-console-log-entry") as HTMLDetailsElement;
    // Open the details and dispatch the native toggle event. (`fireEvent.toggle`
    // doesn't exist in @testing-library/react's preset, but the onToggle handler
    // listens for the bubble-phase 'toggle' event the same way the browser does.)
    entry.open = true;
    entry.dispatchEvent(new Event("toggle", { bubbles: false }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/qa/runs/RUN1/apps/hub/artifacts/console/browser.log",
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/ERROR foo/)).toBeInTheDocument();
    });
  });
});
