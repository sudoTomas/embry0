import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(),
}));

// Mock the axios client so the panel's authenticated fetches go through a
// stub instead of hitting the network. Bearer auth is configured on the real
// `api` module — mocking here lets us assert the panel calls the auth-routed
// path (no `/api/v1` prefix; the real axios client adds it via baseURL).
//
// `vi.mock` factories are hoisted to the top of the file, so we can't
// reference a module-level `const`. Use `vi.hoisted` to declare the spy in a
// hoisted block and share it with the test body.
const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/api/client", () => ({
  api: { get: apiGet },
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
    apiGet.mockReset();
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

  it("fetches the body via axios lazily when the user expands a details block", async () => {
    mockedUseAppArtifacts.mockReturnValue(
      makeQueryResult({ data: ["browser.log"] }),
    );
    apiGet.mockResolvedValue({ data: "ERROR foo\nERROR bar\n" });

    render(<QaConsoleLogPanel runId="RUN1" app="hub" />);
    // No fetch yet — the details block is still closed.
    expect(apiGet).not.toHaveBeenCalled();

    const entry = screen.getByTestId("qa-console-log-entry") as HTMLDetailsElement;
    // Open the details and dispatch the native toggle event. (`fireEvent.toggle`
    // doesn't exist in @testing-library/react's preset, but the onToggle handler
    // listens for the bubble-phase 'toggle' event the same way the browser does.)
    entry.open = true;
    entry.dispatchEvent(new Event("toggle", { bubbles: false }));

    await waitFor(() => {
      // axios baseURL strips `/api/v1` — the panel passes the auth-routed path
      // (no prefix), and axios handles the prefix + Bearer header for us.
      expect(apiGet).toHaveBeenCalledWith(
        "/qa/runs/RUN1/apps/hub/artifacts/console/browser.log",
        expect.objectContaining({ responseType: "text" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/ERROR foo/)).toBeInTheDocument();
    });
  });
});
