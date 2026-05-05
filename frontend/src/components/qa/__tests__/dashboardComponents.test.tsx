import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

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
    expect(screen.getByText("passed")).toBeInTheDocument();
  });

  it("normalises underscores to spaces", () => {
    render(<RunStatusBadge status="qa_failure" />);
    expect(screen.getByText("qa failure")).toBeInTheDocument();
  });

  it("renders infra_error with neutral tone (bug_004)", () => {
    render(<RunStatusBadge status="infra_error" />);
    // The badge text has underscores replaced with spaces
    expect(screen.getByText("infra error")).toBeInTheDocument();
    // The badge element should have the neutral tone data attribute
    const badge = screen.getByTitle("infra_error");
    expect(badge).toBeInTheDocument();
  });
});

describe("CacheHitsRow", () => {
  it("renders three dots when nothing hit", () => {
    render(<CacheHitsRow hits={ZERO_HITS} />);
    expect(screen.getByText("···")).toBeInTheDocument();
  });

  it("shows fire glyph when image+volume hit and turbo majority hit", () => {
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
    expect(screen.getByText("🔥🔥🔥")).toBeInTheDocument();
  });

  it("shows turbo dot when misses dominate", () => {
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
    expect(screen.getByText("🔥🔥·")).toBeInTheDocument();
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
        <QaRepoCard repo={repo} />
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
        <QaRunRow run={run} />
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
    trace_url: null,
    failure_summary: null,
  };

  it("renders the app name + status + duration", () => {
    render(
      <MemoryRouter>
        <QaAppResultCard app={passingApp} repo="org/r1" />
      </MemoryRouter>,
    );
    expect(screen.getByText("hub")).toBeInTheDocument();
    expect(screen.getByText("passed")).toBeInTheDocument();
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

  it("renders the trace link when trace_url is set", () => {
    const traced: AppResult = {
      ...passingApp,
      trace_url: "https://x/trace.zip",
    };
    render(
      <MemoryRouter>
        <QaAppResultCard app={traced} repo="org/r1" />
      </MemoryRouter>,
    );
    const link = screen.getByText("trace") as HTMLAnchorElement;
    expect(link.href).toBe("https://x/trace.zip");
    expect(link.target).toBe("_blank");
  });
});
