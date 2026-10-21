import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

// QaAppResultCard now embeds the artifact panels — those panels call
// `useAppArtifacts`, which is just a react-query hook over `listAppArtifacts`.
// Stub the hook globally so the dashboard-level tests don't need a
// QueryClientProvider just to render a card.
vi.mock("@/hooks/useQaDashboard", () => ({
  useAppArtifacts: vi.fn(() => ({
    data: [],
    isLoading: false,
    isError: false,
  })),
}));

import { CacheHitsRow } from "../CacheHitsRow";
import { QaAppResultCard } from "../QaAppResultCard";
import { QaRepoCard } from "../QaRepoCard";
import { QaRunRow } from "../QaRunRow";
import { RunStatusBadge } from "../RunStatusBadge";
import type {
  AppResult,
  CacheHits,
  RepoEntry,
  RunListItem,
} from "@/lib/types";

const ZERO_HITS: CacheHits = {
  prebaked_image: false,
  shared_volume: false,
  turbo_remote_hits: [],
  turbo_remote_misses: [],
};

describe("RunStatusBadge", () => {
  it("renders the status label", () => {
    render(<RunStatusBadge status="passed" />);
    expect(screen.getByText("Passed")).toBeInTheDocument();
  });

  it("reads as words, not the raw enum", () => {
    render(<RunStatusBadge status="qa_failure" />);
    expect(screen.getByText("QA failure")).toBeInTheDocument();
  });

  it("renders infra_error with neutral tone (bug_004)", () => {
    render(<RunStatusBadge status="infra_error" />);
    // The badge reads as words; the raw value stays in the title
    expect(screen.getByText("Infra error")).toBeInTheDocument();
    // The badge element should have the neutral tone data attribute
    const badge = screen.getByTitle("infra_error");
    expect(badge).toBeInTheDocument();
  });
});

describe("CacheHitsRow", () => {
  it("says every cache missed when nothing hit", () => {
    render(<CacheHitsRow hits={ZERO_HITS} />);
    expect(screen.getByText("Cache: image miss · volume miss · turbo none")).toBeInTheDocument();
  });

  it("spells out image and volume hits and the turbo hit count, never an emoji", () => {
    render(
      <CacheHitsRow
        hits={{
          prebaked_image: true,
          shared_volume: true,
          turbo_remote_hits: ["a", "b"],
          turbo_remote_misses: ["c"],
        }}
      />,
    );
    expect(screen.getByText("Cache: image hit · volume hit · turbo 2/3")).toBeInTheDocument();
  });

  it("counts turbo hits when misses dominate", () => {
    render(
      <CacheHitsRow
        hits={{
          prebaked_image: true,
          shared_volume: true,
          turbo_remote_hits: ["a"],
          turbo_remote_misses: ["b", "c"],
        }}
      />,
    );
    expect(screen.getByText("Cache: image hit · volume hit · turbo 1/3")).toBeInTheDocument();
  });
});

describe("QaRepoCard", () => {
  it("renders the repo name and links to the detail route", () => {
    const repo: RepoEntry = {
      repo: "org/r1",
      latest_run_id: "j-abcdef0123456789",
      latest_status: "passed",
      latest_started_at: "2026-01-01T00:00:00Z",
      latest_app_count: 3,
    };
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <QaRepoCard repo={repo} />
          </tbody>
        </table>
      </MemoryRouter>,
    );
    expect(screen.getByText("org/r1")).toBeInTheDocument();
    expect(screen.getByText(/3 apps/)).toBeInTheDocument();
    const link = screen.getByTestId("qa-repo-card") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/qa/repos/org%2Fr1");
  });
});

describe("QaRunRow", () => {
  it("renders job_id and status, links to the run drilldown", () => {
    const run: RunListItem = {
      job_id: "job-abc",
      repo: "org/r1",
      started_at: "2026-01-01T00:00:00Z",
      overall_status: "failed",
      app_count: 2,
    };
    render(
      <MemoryRouter>
        <table>
          <tbody>
            <QaRunRow run={run} />
          </tbody>
        </table>
      </MemoryRouter>,
    );
    expect(screen.getByText("job-abc")).toBeInTheDocument();
    const link = screen.getByTestId("qa-run-row") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/qa/runs/job-abc");
  });
});

describe("QaAppResultCard", () => {
  const passingApp: AppResult = {
    app_name: "hub",
    status: "passed",
    duration_ms: 1500,
    cache_hits: ZERO_HITS,
    failure_summary: null,
  };

  it("renders the app name + status + duration", () => {
    render(
      <MemoryRouter>
        <QaAppResultCard app={passingApp} repo="org/r1" />
      </MemoryRouter>,
    );
    expect(screen.getByText("hub")).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("1s")).toBeInTheDocument();
  });

  it("renders the failure summary as an alert role when present", () => {
    const failed: AppResult = {
      ...passingApp,
      status: "qa_failure",
      failure_summary: "loads page did not render",
    };
    render(
      <MemoryRouter>
        <QaAppResultCard app={failed} repo="org/r1" />
      </MemoryRouter>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("loads page did not render");
  });

  it("hides the artifacts section when no runId is supplied (back-compat)", () => {
    render(
      <MemoryRouter>
        <QaAppResultCard app={passingApp} repo="org/r1" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("qa-app-card-artifacts")).toBeNull();
  });

  it("shows the artifacts <details> when runId is supplied (closed for passing apps)", () => {
    render(
      <MemoryRouter>
        <QaAppResultCard app={passingApp} repo="org/r1" runId="job-1" />
      </MemoryRouter>,
    );
    const det = screen.getByTestId("qa-app-card-artifacts") as HTMLDetailsElement;
    expect(det).toBeInTheDocument();
    expect(det.open).toBe(false);
    expect(det.getAttribute("data-default-open")).toBe("false");
  });

  it("opens the artifacts <details> by default for failing apps", () => {
    const failed: AppResult = {
      ...passingApp,
      status: "qa_failure",
      failure_summary: "boom",
    };
    render(
      <MemoryRouter>
        <QaAppResultCard app={failed} repo="org/r1" runId="job-1" />
      </MemoryRouter>,
    );
    const det = screen.getByTestId("qa-app-card-artifacts") as HTMLDetailsElement;
    expect(det.open).toBe(true);
    expect(det.getAttribute("data-default-open")).toBe("true");
  });
});
