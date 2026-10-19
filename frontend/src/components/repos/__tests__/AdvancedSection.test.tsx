import { afterEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { AdvancedSection } from "../AdvancedSection";

function renderAt(address: string) {
  return render(
    <MemoryRouter initialEntries={[address]}>
      <AdvancedSection setting="accounts">
        <p>Body</p>
      </AdvancedSection>
    </MemoryRouter>,
  );
}

// jsdom has no scrollIntoView; the first test lends HTMLElement one.
const ownScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");

afterEach(() => {
  if (ownScroll) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", ownScroll);
  else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("AdvancedSection", () => {
  it("lands open, without the opening ease, on an address that names it", async () => {
    const seen: { open: boolean; instant: boolean }[] = [];
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value(this: HTMLDetailsElement) {
        seen.push({ open: this.open, instant: this.hasAttribute("data-instant") });
      },
    });
    renderAt("/repos/acme/widgets#advanced-accounts");
    const section = screen.getByTestId("advanced-accounts") as HTMLDetailsElement;
    // Open, and out of the ease, when the page scrolls to it.
    expect(seen).toEqual([{ open: true, instant: true }]);
    // A frame later it eases again, for the person's own clicks.
    await waitFor(() => expect(section).not.toHaveAttribute("data-instant"));
    expect(section.open).toBe(true);
  });

  it("stays closed, and eases, on an address that names another section", () => {
    renderAt("/repos/acme/widgets#advanced-testSetup");
    const section = screen.getByTestId("advanced-accounts") as HTMLDetailsElement;
    expect(section.open).toBe(false);
    expect(section).not.toHaveAttribute("data-instant");
  });
});
