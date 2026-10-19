import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// jsdom has no scrollIntoView; the tests that scroll lend HTMLElement one.
const ownScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");

afterEach(() => {
  if (ownScroll) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", ownScroll);
  else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** Counts the page's scrolls to a section. */
function countScrolls() {
  const scrolls = { count: 0 };
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value() {
      scrolls.count += 1;
    },
  });
  return scrolls;
}

/** A ResizeObserver the test drives: the returned call tells every observer
 * still connected that the page changed size. */
function driveResizes() {
  const live = new Set<ResizeObserverCallback>();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      report: ResizeObserverCallback;
      constructor(report: ResizeObserverCallback) {
        this.report = report;
      }
      observe() {
        live.add(this.report);
      }
      disconnect() {
        live.delete(this.report);
      }
    },
  );
  return () => live.forEach((report) => report([], {} as ResizeObserver));
}

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

  it("keeps a linked section in place while the page around it changes size", () => {
    const scrolls = countScrolls();
    const resize = driveResizes();
    renderAt("/repos/acme/widgets#advanced-accounts");
    expect(scrolls.count).toBe(1);
    // Parts of the page that load later grow it: the section is brought back.
    resize();
    resize();
    expect(scrolls.count).toBe(3);
  });

  it.each([
    ["scrolls", () => fireEvent.wheel(window)],
    ["clicks", () => fireEvent.pointerDown(document.body)],
    ["types", () => fireEvent.keyDown(document.body, { key: "ArrowDown" })],
  ])("lets go of the section once the person %s", (_, act) => {
    const scrolls = countScrolls();
    const resize = driveResizes();
    renderAt("/repos/acme/widgets#advanced-accounts");
    resize();
    expect(scrolls.count).toBe(2);
    act();
    resize();
    expect(scrolls.count).toBe(2);
  });

  it("lets go of the section once the page has had time to settle", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const scrolls = countScrolls();
    const resize = driveResizes();
    renderAt("/repos/acme/widgets#advanced-accounts");
    vi.advanceTimersByTime(4999);
    resize();
    expect(scrolls.count).toBe(2);
    vi.advanceTimersByTime(1);
    resize();
    expect(scrolls.count).toBe(2);
  });

  it("lets go of the section when the page closes", () => {
    const scrolls = countScrolls();
    const resize = driveResizes();
    const page = renderAt("/repos/acme/widgets#advanced-accounts");
    resize();
    expect(scrolls.count).toBe(2);
    page.unmount();
    resize();
    expect(scrolls.count).toBe(2);
  });

  it("stays closed, and eases, on an address that names another section", () => {
    renderAt("/repos/acme/widgets#advanced-testSetup");
    const section = screen.getByTestId("advanced-accounts") as HTMLDetailsElement;
    expect(section.open).toBe(false);
    expect(section).not.toHaveAttribute("data-instant");
  });
});
