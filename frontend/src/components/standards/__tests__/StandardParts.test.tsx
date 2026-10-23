import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { colourRoles, otherRoles } from "@/lib/standards";
import { ColourRoles, OtherRoles, RulesList, TypeScaleTable, VersionsTable } from "../StandardParts";
import { standard } from "./fixtures";

describe("ColourRoles", () => {
  it("shows each colour role with a swatch and its value in mono", () => {
    render(<ColourRoles roles={colourRoles(standard())} />);
    const items = screen.getAllByTestId("standard-colour");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("color.accent");
    expect(items[0]).toHaveTextContent("#3366ff");
    expect(items[0]).toHaveTextContent("the one accent");
    expect(within(items[0]).getByText("#3366ff").className).toContain("font-mono");
    const swatch = within(items[0]).getByTestId("standard-swatch");
    expect(swatch).toHaveAttribute("data-painted", "true");
    expect(swatch.style.background).not.toBe("");
  });

  it("paints no swatch for a value that is not a plain colour, and names a missing value", () => {
    const s = standard({ values: { "color.base": "url(https://example.com/x.png)" } });
    render(<ColourRoles roles={colourRoles(s)} />);
    const [accent, base] = screen.getAllByTestId("standard-colour");
    expect(within(base).getByTestId("standard-swatch")).toHaveAttribute("data-painted", "false");
    expect(within(base).getByTestId("standard-swatch").style.background).toBe("");
    expect(accent).toHaveTextContent("no value");
  });

  it("says so when the schema declares no colour", () => {
    render(<ColourRoles roles={[]} />);
    expect(screen.getByText("The schema declares no colour roles.")).toBeInTheDocument();
  });
});

describe("OtherRoles", () => {
  it("lists the roles that are not colours with their values", () => {
    render(<OtherRoles roles={otherRoles(standard())} />);
    const table = screen.getByTestId("standard-other-roles");
    expect(table).toHaveTextContent("font.body");
    expect(table).toHaveTextContent("Inter, system-ui, sans-serif");
    expect(table).toHaveTextContent("8px");
    expect(table).toHaveTextContent("cards and tables");
  });
});

describe("TypeScaleTable", () => {
  it("lists the steps largest first with a sample in each step's size", () => {
    render(<TypeScaleTable standard={standard()} />);
    const rows = screen.getAllByTestId("standard-type-step");
    expect(rows.map((r) => r.firstElementChild?.textContent)).toEqual(["h1", "body", "th"]);
    expect(rows[0]).toHaveTextContent("20px");
    expect(rows[0]).toHaveTextContent("700");
    const sample = within(rows[0]).getByText("Aa");
    expect(sample.style.fontSize).toBe("20px");
    expect(sample.style.fontWeight).toBe("700");
    expect(rows[2]).toHaveTextContent("transform: uppercase · letter spacing: 0.06em");
  });

  it("styles no sample from a size that is not plain pixels", () => {
    render(<TypeScaleTable standard={standard({ schema: { type_scale: { odd: { size: "clamp(1rem, 2vw, 3rem)" } } } })} />);
    expect(screen.getByText("Aa").style.fontSize).toBe("");
  });

  it("says so when the schema declares no scale", () => {
    render(<TypeScaleTable standard={standard({ schema: {} })} />);
    expect(screen.getByText("The schema declares no type scale.")).toBeInTheDocument();
  });
});

describe("RulesList", () => {
  it("lists each rule under its id", () => {
    render(<RulesList rules={standard().schema.rules} />);
    const rules = screen.getAllByTestId("standard-rule");
    expect(rules).toHaveLength(2);
    expect(rules[0]).toHaveTextContent("one-accent");
    expect(rules[0]).toHaveTextContent("One accent colour.");
  });

  it("says so when there are none", () => {
    render(<RulesList rules={undefined} />);
    expect(screen.getByText("The schema declares no rules.")).toBeInTheDocument();
  });
});

describe("VersionsTable", () => {
  it("lists each version with its status in words", () => {
    render(
      <VersionsTable
        versions={[standard(), standard({ id: "std-0", version: 1, status: "superseded", created_by: null, created_at: null })]}
      />,
    );
    const rows = screen.getAllByTestId("standard-version-row");
    expect(rows[0]).toHaveTextContent("2");
    expect(rows[0]).toHaveTextContent("Active");
    expect(rows[0]).toHaveTextContent("admin@example.com");
    expect(rows[1]).toHaveTextContent("Superseded");
  });
});
