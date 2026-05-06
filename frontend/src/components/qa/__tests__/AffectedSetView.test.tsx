import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Stub useAffectedSet at the hook module so the view doesn't need a
// QueryClientProvider — same pattern as dashboardComponents.test.tsx.
const mockUseAffectedSet = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useAffectedSet: (runId: string | undefined) => mockUseAffectedSet(runId),
}));

import { AffectedSetView } from "../AffectedSetView";
import type { AffectedSetResponse } from "@/lib/types";

const FULL: AffectedSetResponse = {
  job_id: "run-md-1",
  apps_to_qa: ["hub", "companion"],
  apps_skipped: ["lane"],
  force_all_apps: false,
  changed_files: [
    "apps/hub/app/page.tsx",
    "packages/types/src/index.ts",
  ],
  base_branch: "main",
  dep_graph: [],
};

beforeEach(() => {
  mockUseAffectedSet.mockReset();
});

describe("AffectedSetView", () => {
  it("renders all three sections with counts and the diff base", () => {
    mockUseAffectedSet.mockReturnValue({
      data: FULL,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AffectedSetView runId="run-md-1" />);

    // Header surfaces job_id and base_branch.
    expect(screen.getByText("run-md-1")).toBeInTheDocument();
    expect(screen.getByText(/base:/)).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();

    // No force-all-apps badge when force_all_apps=false.
    expect(screen.queryByTestId("force-all-apps-badge")).toBeNull();

    // Three list sections with correct counts.
    const runSec = screen.getByTestId("apps-run-section");
    expect(runSec).toHaveTextContent("Apps run (2)");
    expect(runSec).toHaveTextContent("hub");
    expect(runSec).toHaveTextContent("companion");

    const skipSec = screen.getByTestId("apps-skipped-section");
    expect(skipSec).toHaveTextContent("Apps skipped (1)");
    expect(skipSec).toHaveTextContent("lane");

    const filesSec = screen.getByTestId("changed-files-section");
    expect(filesSec).toHaveTextContent("Changed files (2)");
    expect(filesSec).toHaveTextContent("apps/hub/app/page.tsx");
    expect(filesSec).toHaveTextContent("packages/types/src/index.ts");

    // Empty dep_graph shows the placeholder hint.
    const depSec = screen.getByTestId("dep-graph-section");
    expect(depSec).toHaveTextContent("Dep graph (0)");
    expect(depSec).toHaveTextContent(/not yet exposed/i);
  });

  it("shows the force-all-apps badge when the flag is set", () => {
    mockUseAffectedSet.mockReturnValue({
      data: {
        ...FULL,
        force_all_apps: true,
        apps_skipped: [],
        changed_files: [],
        base_branch: "",
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<AffectedSetView runId="run-md-1" />);
    expect(screen.getByTestId("force-all-apps-badge")).toBeInTheDocument();
    // Empty changed_files renders the empty-state hint, not the count alone.
    expect(screen.getByTestId("changed-files-section")).toHaveTextContent(
      /No files in diff/i,
    );
  });

  it("renders dep_graph entries as source -> target rows when non-empty", () => {
    mockUseAffectedSet.mockReturnValue({
      data: {
        ...FULL,
        dep_graph: [
          { source: "@x/hub", target: "@x/types" },
          { source: "@x/companion", target: "@x/types" },
        ],
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<AffectedSetView runId="run-md-1" />);
    const depSec = screen.getByTestId("dep-graph-section");
    expect(depSec).toHaveTextContent("Dep graph (2)");
    expect(depSec).toHaveTextContent("@x/hub");
    expect(depSec).toHaveTextContent("@x/types");
    expect(depSec).toHaveTextContent("@x/companion");
  });

  it("renders the loading skeleton while data is unresolved", () => {
    mockUseAffectedSet.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const { container } = render(<AffectedSetView runId="run-md-1" />);
    // No content section should render yet.
    expect(container.querySelector('[data-testid="affected-set-view"]')).toBeNull();
  });

  it("shows a 404 empty-state when the run has no metadata row", () => {
    mockUseAffectedSet.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      // Mimic the axios error shape the component reads from.
      error: { response: { status: 404 } },
      refetch: vi.fn(),
    });
    render(<AffectedSetView runId="run-md-1" />);
    expect(
      screen.getByText(/No affected-set recorded/i),
    ).toBeInTheDocument();
  });

  it("shows a generic error when the load fails for non-404 reasons", () => {
    mockUseAffectedSet.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { response: { status: 500 } },
      refetch: vi.fn(),
    });
    render(<AffectedSetView runId="run-md-1" />);
    expect(
      screen.getByText(/Failed to load affected-set/i),
    ).toBeInTheDocument();
  });
});
