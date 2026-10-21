import { describe, expect, it } from "vitest";
import indexHtml from "../../index.html?raw";
import faviconSvg from "../../public/favicon.svg?raw";
import indexCss from "../index.css?raw";

/**
 * The browser tab and the home screen show the embry0 mark. index.html links
 * the icons and every icon it links ships in public/.
 */

const PUBLIC_FILES = new Set(
  Object.keys(import.meta.glob("/public/*")).map((path) => path.replace(/^\/public/, "")),
);

const head = new DOMParser().parseFromString(indexHtml, "text/html").head;

function link(selector: string): HTMLLinkElement {
  const el = head.querySelector<HTMLLinkElement>(selector);
  if (!el) throw new Error(`index.html has no ${selector}`);
  return el;
}

describe("index.html icons", () => {
  it("links the SVG mark as the tab icon", () => {
    expect(link('link[rel="icon"][type="image/svg+xml"]').getAttribute("href")).toBe("/favicon.svg");
  });

  it("links a 32 px PNG for browsers without SVG icons", () => {
    expect(link('link[rel="icon"][type="image/png"][sizes="32x32"]').getAttribute("href")).toBe(
      "/favicon-32.png",
    );
  });

  it("lists the SVG icon last, so browsers that take the last icon take the SVG", () => {
    const icons = [...head.querySelectorAll('link[rel="icon"]')];
    expect(icons.at(-1)?.getAttribute("type")).toBe("image/svg+xml");
  });

  it("links the home-screen icon", () => {
    expect(link('link[rel="apple-touch-icon"]').getAttribute("href")).toBe("/apple-touch-icon.png");
  });

  it("colours the browser chrome with the page colour", () => {
    // The page background is index.css's --color-background token; the
    // theme-color meta is a copy of it, which is how it drifts.
    const background = /--color-background:\s*(#[0-9a-f]{6})\b/i.exec(indexCss)?.[1];
    expect(background).toBeDefined();
    expect(head.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe(background);
  });

  it("ships every icon it links, and the /favicon.ico browsers ask for unasked", () => {
    const hrefs = [...head.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]')].map(
      (el) => el.getAttribute("href") ?? "",
    );
    expect(hrefs.length).toBeGreaterThanOrEqual(3);
    for (const href of [...hrefs, "/favicon.ico"]) {
      expect(PUBLIC_FILES, `public${href} is missing`).toContain(href);
    }
  });

  it("darkens the favicon's dot on a light tab bar, where the cream dot vanished", () => {
    const svg = new DOMParser().parseFromString(faviconSvg, "image/svg+xml");
    const css = svg.querySelector("style")?.textContent ?? "";
    expect(css).toMatch(/@media\s*\(prefers-color-scheme:\s*light\)\s*\{\s*\.d\s*\{\s*fill:\s*#0a0a0f/);
    expect(svg.querySelectorAll("path.d")).toHaveLength(1);
  });
});
