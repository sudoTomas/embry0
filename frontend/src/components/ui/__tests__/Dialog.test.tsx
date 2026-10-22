import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import css from "../../../index.css?raw";
import { Dialog } from "../Dialog";

function sheetRules(): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const rules = Array.from(style.sheet?.cssRules ?? []);
  style.remove();
  return rules;
}

/** A page with an opener, so focus has somewhere to come from and go back to. */
function Page({ side }: { side?: "center" | "right" | "left" }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>Open</button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Delete host" side={side}>
        <div className="p-5">
          <button type="button">Keep</button>
          <button type="button">Delete</button>
        </div>
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("is a modal dialog over the window, named by its title", () => {
    render(<Dialog onClose={vi.fn()} title="Delete host">body</Dialog>);
    const dialog = screen.getByRole("dialog", { name: "Delete host" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("heading", { name: "Delete host" })).toBeInTheDocument();
    expect(dialog.parentElement?.parentElement, "the layer is a child of body (Overlay)").toBe(document.body);
    expect(dialog).toHaveClass("dialog-in");
  });

  it("the Esc hint is decoration for screen readers", () => {
    render(<Dialog onClose={vi.fn()} title="Delete host">body</Dialog>);
    const hint = screen.getByRole("dialog").querySelector("kbd");
    expect(hint?.textContent).toBe("Esc");
    expect(hint, "the hint repeats what Escape does; it is not text to read out").toHaveAttribute("aria-hidden", "true");
  });

  it("renders nothing while closed", () => {
    render(<Dialog open={false} onClose={vi.fn()} title="Delete host">body</Dialog>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });

  it("a label outranks the title for the dialog's name", () => {
    render(<Dialog onClose={vi.fn()} label="Stored session of Developer for this job" title="Stored session">body</Dialog>);
    expect(screen.getByRole("dialog", { name: "Stored session of Developer for this job" })).toBeInTheDocument();
  });

  it("closes on Escape, on the backdrop and on Close, not on a click inside", () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} title="Delete host"><button type="button">Keep</button></Dialog>);
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onClose, "a click inside the panel is not a dismissal").not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose, "the backdrop closes").toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("can refuse the backdrop", () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} title="Delete host" dismissOnBackdrop={false}>body</Dialog>);
    fireEvent.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("moves focus in, keeps Tab inside, and gives focus back to the opener", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    const seen = new Set<Element | null>();
    for (let i = 0; i < 6; i++) {
      await user.tab();
      seen.add(document.activeElement);
    }
    expect([...seen].every((el) => dialog.contains(el)), "Tab never leaves the dialog").toBe(true);
    expect(seen.size, "Close, Keep and Delete are the three stops").toBe(3);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement, "focus returns to what opened the dialog").toBe(opener);
  });

  it("locks the body's scroll while open and gives back what it found", async () => {
    const user = userEvent.setup();
    document.body.style.overflow = "auto";
    render(<Page />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(document.body.style.overflow, "the value before the dialog, not an empty string").toBe("auto");
    // A second dialog in turn locks and releases again.
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(document.body.style.overflow).toBe("auto");
    document.body.style.overflow = "";
  });

  it("a drawer sits at the window's edge with no gutter", () => {
    render(<Dialog onClose={vi.fn()} title="Templates" side="left">body</Dialog>);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("dialog-in-left", "h-full");
    expect(dialog.parentElement).toHaveClass("p-0", "justify-start");
    expect(dialog).not.toHaveClass("rounded-lg");
  });

  it("keeps its entrances in index.css, moving only transform and opacity, for people who did not ask for reduced motion", () => {
    const rules = sheetRules();
    for (const name of ["dialog-in", "dialog-in-right", "dialog-in-left"]) {
      const keyframes = rules.filter((r): r is CSSKeyframesRule => r instanceof CSSKeyframesRule && r.name === name);
      expect(keyframes, `one @keyframes ${name}`).toHaveLength(1);
      const props = Array.from(keyframes[0].cssRules).flatMap((f) => Array.from((f as CSSKeyframeRule).style));
      expect(props.length).toBeGreaterThan(0);
      expect(props.filter((p) => p !== "transform" && p !== "opacity"), `${name} animates only transform and opacity`).toEqual([]);
      const owners = rules.flatMap((rule) => {
        const inner = rule instanceof CSSMediaRule ? Array.from(rule.cssRules) : [rule];
        return inner
          .filter((r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === `.${name}`)
          .map(() => (rule instanceof CSSMediaRule ? rule.media.mediaText : "all"));
      });
      expect(owners, `.${name} runs only under the reduced-motion gate`).toEqual(["(prefers-reduced-motion: no-preference)"]);
      const fill = rules
        .flatMap((rule) => (rule instanceof CSSMediaRule ? Array.from(rule.cssRules) : [rule]))
        .filter((r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === `.${name}`)
        .map((r) => r.style.animationFillMode || r.style.animation);
      expect(fill.join(" "), "no forwards fill: a kept transform would become a containing block").not.toMatch(/forwards|both/);
    }
  });
});
