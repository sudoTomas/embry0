import { describe, expect, it } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
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

/** Longer than the 150 ms close, so whatever it does has happened. */
const afterTheClose = () => new Promise((resolve) => setTimeout(resolve, 200));

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
