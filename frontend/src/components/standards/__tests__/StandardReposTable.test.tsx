import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

let narrow = false;
vi.mock("@/hooks/useIsNarrow", () => ({ useIsNarrow: () => narrow }));

const mockSetMode = vi.fn();
const mockAudit = vi.fn();
vi.mock("@/api/standards", async () => {
  const actual = await vi.importActual<typeof import("@/api/standards")>("@/api/standards");
  return {
    ...actual,
    setRepoStandardMode: (...a: unknown[]) => mockSetMode(...a),
    startStandardAudit: (...a: unknown[]) => mockAudit(...a),
  };
});

import { standardRows, type StandardRepoRow } from "@/lib/standards";
import { StandardReposTable } from "../StandardReposTable";
import { state } from "./fixtures";

const ROWS: StandardRepoRow[] = standardRows(
  "acme",
  "design",
  [{ repo: "acme/Low" }, { repo: "acme/running" }, { repo: "acme/fresh" }, { repo: "acme/quiet" }],
  [
    state("acme/low", { mode: "advise", score: 31, findings_open: 7, last_audit_at: "2026-01-01T00:00:00Z", last_audit_job_id: "job-old" }),
    state("acme/running", { mode: "advise", last_audit_job_id: "job-run" }),
    state("acme/fresh", { mode: "enforce" }),
  ],
);

function renderTable(props: Partial<Parameters<typeof StandardReposTable>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StandardReposTable
          owner="acme"
          kind="design"
          rows={ROWS}
          hasStandard
          isAdmin
          canAudit
          jobStatuses={new Map([["job-old", "completed"], ["job-run", "running"]])}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function rowOf(repo: string) {
  return screen.getAllByTestId("standard-repo-row").find((el) => el.getAttribute("data-repo") === repo)!;
}

const shownRepos = () => screen.queryAllByTestId("standard-repo-row").map((r) => r.getAttribute("data-repo"));

/** Unfold the repositories in mode off that no audit scored. */
function showOff() {
  fireEvent.click(screen.getByTestId("standard-repos-off-toggle"));
}

beforeEach(() => {
  narrow = false;
  mockSetMode.mockReset().mockResolvedValue({});
  mockAudit.mockReset().mockResolvedValue({ job_id: "job-new" });
});

describe("StandardReposTable", () => {
  it("lists the repositories furthest from the standard first, by their name under the owner", () => {
    renderTable();
    showOff();
    const rows = screen.getAllByTestId("standard-repo-row");
    expect(rows.map((r) => r.getAttribute("data-repo"))).toEqual(["acme/Low", "acme/fresh", "acme/running", "acme/quiet"]);
    expect(within(rows[0]).getByTestId("standard-repo-link")).toHaveTextContent(/^Low$/);
    expect(within(rows[0]).getByTestId("standard-repo-link")).toHaveAttribute("href", "/repos/acme/Low");
    expect(within(rows[0]).getByTestId("standard-score")).toHaveTextContent("31");
    expect(within(rows[0]).getByTestId("standard-findings")).toHaveTextContent("7");
    // Never audited: no findings count to show yet.
    expect(within(rows[1]).getByTestId("standard-findings")).toHaveTextContent("-");
    expect(screen.getByTestId("standard-repos-counts")).toHaveTextContent("4 repositories · 1 enforce · 2 advise · 1 off");
  });

  it("folds the repositories in mode off that no audit scored behind one row", () => {
    const rows = standardRows(
      "acme",
      "design",
      [{ repo: "acme/held" }, { repo: "acme/was-held" }, ...Array.from({ length: 140 }, (_, i) => ({ repo: `acme/off-${i}` }))],
      [
        state("acme/held", { mode: "advise" }),
        // Audited, then switched off: its score is still worth reading.
        state("acme/was-held", { score: 44, last_audit_at: "2026-01-01T00:00:00Z" }),
      ],
    );
    renderTable({ rows });
    expect(shownRepos()).toEqual(["acme/was-held", "acme/held"]);
    const toggle = screen.getByTestId("standard-repos-off-toggle");
    expect(toggle).toHaveTextContent("Show 140 in mode off");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.className).not.toContain("bg-primary");
    // The counts still cover every repository.
    expect(screen.getByTestId("standard-repos-counts")).toHaveTextContent("142 repositories · 0 enforce · 1 advise · 141 off");

    showOff();
    expect(shownRepos()).toHaveLength(142);
    expect(shownRepos().slice(0, 3)).toEqual(["acme/was-held", "acme/held", "acme/off-0"]);
    // The toggle stays where it was clicked, between the held and the folded rows.
    const toggleRow = screen.getByTestId("standard-repos-off-row");
    expect(toggleRow.previousElementSibling).toHaveAttribute("data-repo", "acme/held");
    expect(toggleRow.nextElementSibling).toHaveAttribute("data-repo", "acme/off-0");
    expect(screen.getByTestId("standard-repos-off-toggle")).toHaveTextContent("Hide the 140 in mode off");

    fireEvent.click(screen.getByTestId("standard-repos-off-toggle"));
    expect(shownRepos()).toHaveLength(2);
  });

  it("searches the folded repositories too when filtering by name", () => {
    renderTable();
    expect(shownRepos()).not.toContain("acme/quiet");
    fireEvent.change(screen.getByTestId("standard-repos-filter"), { target: { value: "qui" } });
    expect(shownRepos()).toEqual(["acme/quiet"]);
    expect(screen.queryByTestId("standard-repos-off-toggle")).toBeNull();
  });

  it("says when no repository is held to the standard, with the others one click away", () => {
    renderTable({ rows: standardRows("acme", "design", [{ repo: "acme/a" }, { repo: "acme/b" }], []) });
    expect(screen.getByTestId("standard-repos-none-held")).toHaveTextContent("No repository is held to this standard yet");
    expect(shownRepos()).toEqual([]);
    showOff();
    expect(shownRepos()).toEqual(["acme/a", "acme/b"]);
  });

  it("offers no fold when nothing is in mode off", () => {
    renderTable({ rows: ROWS.filter((r) => r.state.mode !== "off") });
    expect(screen.queryByTestId("standard-repos-off-row")).toBeNull();
    expect(shownRepos()).toHaveLength(3);
  });

  it("links each repository's newest audit job with its status", () => {
    renderTable();
    const job = within(rowOf("acme/running")).getByTestId("standard-audit-job");
    expect(job).toHaveAttribute("href", "/jobs/job-run");
    expect(job).toHaveTextContent("Running");
  });

  it("lets an admin set a repository's mode with the registry's spelling", async () => {
    renderTable();
    fireEvent.change(within(rowOf("acme/Low")).getByTestId("standard-mode-select"), { target: { value: "enforce" } });
    await waitFor(() => expect(mockSetMode).toHaveBeenCalledWith("acme", "design", "acme/Low", "enforce"));
  });

  it("shows a mode refusal in the server's words under its row", async () => {
    mockSetMode.mockRejectedValue({ response: { status: 403, data: { detail: "admin tier required" } } });
    renderTable();
    showOff();
    fireEvent.change(within(rowOf("acme/quiet")).getByTestId("standard-mode-select"), { target: { value: "advise" } });
    expect(await screen.findByTestId("standard-repo-error")).toHaveTextContent("acme/quiet: admin tier required");
  });

  it("shows the mode as words to a session that cannot change it", () => {
    renderTable({ isAdmin: false });
    expect(screen.queryByTestId("standard-mode-select")).toBeNull();
    expect(within(rowOf("acme/fresh")).getByTestId("standard-mode")).toHaveTextContent("Enforce");
    expect(screen.getByTestId("standard-mode-admin-note")).toBeInTheDocument();
  });

  it("disables Audit in mode off and while an audit is pending or running", () => {
    renderTable({ jobStatuses: new Map([["job-old", "completed"], ["job-run", "pending"]]) });
    showOff();
    const button = (repo: string) => within(rowOf(repo)).getByTestId("standard-audit-button");
    expect(button("acme/quiet")).toBeDisabled();
    expect(button("acme/quiet")).toHaveAttribute("title", expect.stringMatching(/mode off/));
    expect(button("acme/running")).toBeDisabled();
    expect(button("acme/running")).toHaveAttribute("title", "An audit is already pending.");
    expect(button("acme/Low")).toBeEnabled();
    expect(button("acme/fresh")).toBeEnabled();
  });

  it("disables every Audit without an active standard", () => {
    renderTable({ hasStandard: false });
    for (const b of screen.getAllByTestId("standard-audit-button")) expect(b).toBeDisabled();
  });

  it("starts an audit and shows a 409 refusal's detail under its row", async () => {
    mockAudit.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "an audit of acme/Low is already running: job job-x" } },
    });
    renderTable();
    fireEvent.click(within(rowOf("acme/Low")).getByTestId("standard-audit-button"));
    await waitFor(() => expect(mockAudit).toHaveBeenCalledWith("acme", "design", "acme/Low"));
    const error = await screen.findByTestId("standard-repo-error");
    expect(within(error).getByRole("alert")).toHaveTextContent("acme/Low: an audit of acme/Low is already running: job job-x");

    fireEvent.click(within(rowOf("acme/Low")).getByTestId("standard-audit-button"));
    await waitFor(() => expect(mockAudit).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("standard-repo-error")).toBeNull());
  });

  it("puts the one gold on the next repository to audit, and none when there is none", () => {
    const { unmount } = renderTable();
    const gold = screen.getAllByTestId("standard-audit-button").filter((b) => b.getAttribute("data-gold") === "true");
    expect(gold).toHaveLength(1);
    expect(rowOf("acme/fresh")).toContainElement(gold[0]);
    expect(gold[0].className).toContain("bg-primary");
    expect(within(rowOf("acme/Low")).getByTestId("standard-audit-button").className).not.toContain("bg-primary");
    unmount();

    renderTable({ canAudit: false });
    expect(screen.getAllByTestId("standard-audit-button").filter((b) => b.getAttribute("data-gold") === "true")).toEqual([]);
  });

  it("moves the mode under the name on a phone, so the score and Audit stay in view", () => {
    narrow = true;
    renderTable();
    expect(screen.queryByRole("columnheader", { name: "Mode" })).toBeNull();
    const nameCell = within(rowOf("acme/Low")).getByTestId("standard-repo-link").closest("td")!;
    expect(within(nameCell).getByTestId("standard-mode-select")).toHaveValue("advise");
    expect(within(rowOf("acme/Low")).getAllByRole("cell")).toHaveLength(5);
  });

  it("filters by name", () => {
    renderTable();
    fireEvent.change(screen.getByTestId("standard-repos-filter"), { target: { value: "fre" } });
    expect(screen.getAllByTestId("standard-repo-row")).toHaveLength(1);
    expect(screen.getByTestId("standard-repos-counts")).toHaveTextContent("1 of 4");
    fireEvent.change(screen.getByTestId("standard-repos-filter"), { target: { value: "zzz" } });
    expect(screen.getByText("No repository matches this filter.")).toBeInTheDocument();
  });

  it("says so when the owner has no registered repository", () => {
    renderTable({ rows: [] });
    expect(screen.getByText("No registered repositories under this owner")).toBeInTheDocument();
  });
});
