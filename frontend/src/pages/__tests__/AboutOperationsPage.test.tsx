import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { AboutOperationsPage } from "../AboutOperationsPage";

// The operations page is lore: its figures and their chrome are decoration,
// and decoration is never gold. The page holds no action, so it spends no
// gold at all.
const GOLD = /(^|\s)(bg|text|border|fill|stroke)-primary(\/\d+)?(\s|$)/;

describe("AboutOperationsPage", () => {
  it("draws its figures and their text without the gold", () => {
    const { container } = render(<AboutOperationsPage />);
    expect(container.querySelectorAll("figure").length, "no figures rendered").toBe(8);
    const gold = [...container.querySelectorAll("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));
    expect(gold.map((el) => `${el.tagName.toLowerCase()}.${el.getAttribute("class")}`)).toEqual([]);
  });
});
