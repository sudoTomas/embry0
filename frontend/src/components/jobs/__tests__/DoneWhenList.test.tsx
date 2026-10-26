import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { parseDoneWhenLines } from "@/lib/doneWhen";
import type { DoneWhen } from "@/lib/types/jobs";
import { DoneWhenList } from "../DoneWhenList";

const LIST: DoneWhen = {
  items: [
    { id: "d1", done: "The boot failure's cause is logged in full", shown_by: "a test with a bad config" },
    { id: "d2", done: "The cause is written down", shown_by: "a PR section" },
  ],
  out_of_scope: ["Deploy tooling"],
  source: "triage",
  edited_by: null,
  edited_at: null,
  report: {
    items: [
      { id: "d1", met: true, evidence: "test_bad_config passes" },
      { id: "d2", met: false, evidence: "not reported" },
    ],
    unmet: ["d2"],
    reported: true,
    checked_at: "2026-10-26T03:00:00+00:00",
  },
};

describe("DoneWhenList (done when, spec 2026-10-26)", () => {
  it("lists each condition with how it is shown and whether the run met it", () => {
    render(<DoneWhenList doneWhen={LIST} />);
    const [first, second] = screen.getAllByTestId("done-when-item");
    expect(first).toHaveAttribute("data-met", "true");
    expect(first).toHaveTextContent("The boot failure's cause is logged in full — shown by a test with a bad config");
    expect(first).toHaveTextContent("test_bad_config passes");
    expect(second).toHaveAttribute("data-met", "false");
    expect(within(second).getByLabelText("not met")).toBeInTheDocument();
    expect(screen.getByTestId("job-done-when")).toHaveTextContent("drafted by triage");
    expect(screen.getByText("Out of scope (1)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("edits the list one line per condition and saves it", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<DoneWhenList doneWhen={LIST} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const conditions = screen.getByLabelText("Done-when conditions");
    expect(conditions).toHaveValue(
      "The boot failure's cause is logged in full — a test with a bad config\nThe cause is written down — a PR section",
    );
    await userEvent.clear(conditions);
    await userEvent.type(conditions, "Only the header row -- the CSV test passes");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({
      items: [{ done: "Only the header row", shown_by: "the CSV test passes" }],
      out_of_scope: ["Deploy tooling"],
    });
    expect(screen.queryByTestId("done-when-editor")).toBeNull();
  });

  it("refuses an empty list and shows nothing for a finished job without one", async () => {
    const onSave = vi.fn();
    const { unmount } = render(<DoneWhenList doneWhen={LIST} onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getByLabelText("Done-when conditions"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText("Add at least one condition.")).toBeInTheDocument();
    unmount();
    const { container } = render(<DoneWhenList doneWhen={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("parseDoneWhenLines", () => {
  it("splits a condition from how it is shown and drops bullets and blank lines", () => {
    expect(parseDoneWhenLines("- A — b\n\nC | d\nE", "  x\n")).toEqual({
      items: [
        { done: "A", shown_by: "b" },
        { done: "C", shown_by: "d" },
        { done: "E", shown_by: "" },
      ],
      out_of_scope: ["x"],
    });
  });
});
