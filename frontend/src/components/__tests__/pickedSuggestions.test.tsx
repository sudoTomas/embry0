import { describe, expect, it } from "vitest";
import { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RepoSelector } from "@/components/issues/RepoSelector";
import { LabelInput } from "@/components/issues/LabelInput";
import { TagInput } from "@/components/ui/TagInput";

/**
 * Each input lists suggestions while it has focus and closes the list 150 ms
 * after it loses focus, so a click on a suggestion lands first. A suggestion
 * picked after typing a filter must outlast that close; the repository
 * picker used to put the typed filter back (#267).
 */

/**
 * Longer than the 150 ms close, so the close has run, and inside ``act``, so React has rendered what it set before
 * the test reads. On a loaded host the event loop can stall past both timers: they then run back to back, and a
 * plain wait let the test read before React rendered the close. A check that a pick is kept then read the pick
 * itself, before the close could undo it, and passed whatever the close did (#267 back passed); a check of a value
 * the close sets read nothing yet, and failed.
 */
const afterTheClose = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 200)));

/** What a loaded host does to the event loop: nothing else runs for ``ms``. */
function stall(ms: number) {
  const from = Date.now();
  while (Date.now() - from < ms) {
    // busy
  }
}

function Picker({ initial = "" }: { initial?: string }) {
  const [repo, setRepo] = useState(initial);
  return (
    <>
      <RepoSelector value={repo} onChange={setRepo} repos={["acme/widgets", "acme/gadgets"]} />
      <output aria-label="chosen">{repo}</output>
    </>
  );
}

function Labels() {
  const [labels, setLabels] = useState<string[]>([]);
  return (
    <>
      <LabelInput value={labels} onChange={setLabels} suggestions={["bug", "docs"]} />
      <output aria-label="chosen">{labels.join(",")}</output>
    </>
  );
}

function Tags() {
  const [tags, setTags] = useState<string[]>([]);
  return (
    <>
      <TagInput id="tags" value={tags} onChange={setTags} suggestions={["bug", "docs"]} commitOnBlur />
      <output aria-label="chosen">{tags.join(",")}</output>
    </>
  );
}

const chosen = () => screen.getByRole("status", { name: "chosen" });

describe("RepoSelector", () => {
  it("keeps the repository picked from the list after typing a filter", async () => {
    render(<Picker />);
    await userEvent.type(screen.getByPlaceholderText("Select repository..."), "wid");
    await userEvent.click(screen.getByRole("button", { name: "acme/widgets" }));
    await afterTheClose();
    expect(chosen()).toHaveTextContent(/^acme\/widgets$/);
    expect(screen.getByPlaceholderText("Select repository...")).toHaveValue("acme/widgets");
  });

  it("keeps it when the event loop stalls past the close, as on a loaded host", async () => {
    // The check must still read after the close has rendered, or it reads the pick before the close could undo it.
    render(<Picker />);
    await userEvent.type(screen.getByPlaceholderText("Select repository..."), "wid");
    await userEvent.click(screen.getByRole("button", { name: "acme/widgets" }));
    const waited = afterTheClose();
    stall(300);
    await waited;
    expect(chosen()).toHaveTextContent(/^acme\/widgets$/);
    expect(screen.getByPlaceholderText("Select repository...")).toHaveValue("acme/widgets");
  });

  it("keeps a repository picked over one already chosen", async () => {
    render(<Picker initial="acme/gadgets" />);
    const input = screen.getByPlaceholderText("Select repository...");
    await userEvent.clear(input);
    await userEvent.type(input, "wid");
    await userEvent.click(screen.getByRole("button", { name: "acme/widgets" }));
    await afterTheClose();
    expect(chosen()).toHaveTextContent(/^acme\/widgets$/);
  });

  it("still takes a name typed in full when the person tabs away", async () => {
    render(<Picker />);
    await userEvent.type(screen.getByPlaceholderText("Select repository..."), "acme/other");
    await userEvent.tab();
    await afterTheClose();
    expect(chosen()).toHaveTextContent(/^acme\/other$/);
  });

  it("still takes it when the event loop stalls past the close, as on a loaded host", async () => {
    // The 150 ms close and the 200 ms wait both come due at once: without act the check read before React rendered
    // the name the close set, and the test failed in a full run under load.
    render(<Picker />);
    await userEvent.type(screen.getByPlaceholderText("Select repository..."), "acme/other");
    await userEvent.tab();
    const waited = afterTheClose();
    stall(300);
    await waited;
    expect(chosen()).toHaveTextContent(/^acme\/other$/);
  });
});

describe("LabelInput", () => {
  it("adds the label picked from the list, and not the filter typed to find it", async () => {
    render(<Labels />);
    await userEvent.type(screen.getByLabelText("Add a label"), "bu");
    await userEvent.click(screen.getByRole("button", { name: "bug" }));
    await afterTheClose();
    expect(chosen()).toHaveTextContent(/^bug$/);
  });
});

describe("TagInput", () => {
  it("adds the tag picked from the list, and not the filter typed to find it", async () => {
    render(<Tags />);
    await userEvent.type(screen.getByRole("textbox"), "bu");
    await userEvent.click(screen.getByRole("button", { name: "bug" }));
    await afterTheClose();
    expect(chosen()).toHaveTextContent(/^bug$/);
  });
});
