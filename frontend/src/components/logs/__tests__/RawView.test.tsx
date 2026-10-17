import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VirtuosoMockContext } from "react-virtuoso";
import type { ReactElement } from "react";
import { RawView } from "../RawView";
import { loadFixture } from "@/lib/__tests__/fixtures";

// react-virtuoso measures its viewport; jsdom has none, so without this
// context it renders zero rows and every assertion below would pass vacuously.
function renderVirtual(ui: ReactElement, itemCount = 200) {
  return render(
    <VirtuosoMockContext.Provider value={{ viewportHeight: 40 * itemCount, itemHeight: 40 }}>{ui}</VirtuosoMockContext.Provider>,
  );
}

describe("RawView", () => {
  it("renders nothing without a Virtuoso viewport (why the mock context is required)", () => {
    render(<RawView events={loadFixture("issue-to-pr")} />);
    expect(screen.queryAllByTestId("raw-row")).toHaveLength(0);
  });

  it("renders one row per event", () => {
    const events = loadFixture("issue-to-pr");
    renderVirtual(<RawView events={events} />);
    expect(screen.getAllByTestId("raw-row")).toHaveLength(events.length);
  });

  it("shows a tool_call's type, #seq and summary", () => {
    renderVirtual(<RawView events={loadFixture("issue-to-pr")} />);
    const row = screen.getAllByTestId("raw-row").find((r) => r.textContent?.includes("#1015"));
    expect(row, "no row carries #1015").toBeDefined();
    expect(row!.textContent).toContain("tool_call");
    expect(row!.textContent).toContain("Read README.md");
  });

  it("shows a progress row's message, never undefined", () => {
    renderVirtual(<RawView events={loadFixture("qa")} />);
    const row = screen.getAllByTestId("raw-row").find((r) => r.textContent?.includes("progress"));
    expect(row, "no progress row").toBeDefined();
    expect(row!.textContent).toContain("Running acceptance criteria");
    expect(document.body.textContent).not.toContain("undefined");
  });

  it("pre-contract job: the error line shows its text and the tool-call line its summary", () => {
    const events = loadFixture("legacy-rows");
    renderVirtual(<RawView events={events} />);
    const rows = screen.getAllByTestId("raw-row");
    expect(rows).toHaveLength(events.length);
    const errorRow = rows.find((r) => r.textContent?.includes("legacy boom"));
    expect(errorRow?.textContent ?? "", "the legacy error row lost its #seq").toContain("#22");
    const toolRow = rows.find((r) => r.textContent?.includes("#16"));
    expect(toolRow?.textContent ?? "", "the legacy tool_call row lost its summary").toContain("Bash ls -la");
  });

  it("pre-contract job: no row prints undefined or an empty payload", () => {
    renderVirtual(<RawView events={loadFixture("legacy-rows")} />);
    const rows = screen.getAllByTestId("raw-row");
    expect(document.body.textContent, "a legacy row printed undefined").not.toContain("undefined");
    const started = rows.find((r) => r.textContent?.includes("#10"));
    expect(started?.textContent ?? "", "a legacy node_started without attempt").toMatch(/node_started developer$/);
    const agent = rows.find((r) => r.textContent?.includes("#13"));
    expect(agent?.textContent ?? "", "a legacy agent_started has no headline").toMatch(/agent_started developer$/);
  });

  it("an agent_started row names its agent and model", () => {
    renderVirtual(<RawView events={loadFixture("issue-to-pr")} />);
    const row = screen.getAllByTestId("raw-row").find((r) => r.textContent?.includes("agent_started"));
    expect(row?.textContent ?? "", "the agent_started headline").toContain("agent_started triage claude-sonnet-5-5");
  });

  it("an undecodable row renders as a system line with #seq and its text", () => {
    renderVirtual(<RawView events={loadFixture("legacy-rows")} />);
    const row = screen.getAllByTestId("raw-row").find((r) => r.textContent?.includes("this line was never JSON"));
    expect(row, "the raw row is missing").toBeDefined();
    expect(row!.textContent, "the raw row lost its #seq").toContain("#25");
    expect(row!.textContent).toContain("system");
  });

  it("an event with an empty timestamp never prints Invalid Date", () => {
    renderVirtual(<RawView events={loadFixture("legacy-rows")} />);
    expect(document.body.textContent, "an empty timestamp was formatted as a date").not.toContain("Invalid Date");
    expect(document.body.textContent).toContain("--:--:--");
  });
});
