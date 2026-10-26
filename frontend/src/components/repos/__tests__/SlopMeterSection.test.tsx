import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SlopMeter } from "@/api/slopMeter";

const state: { data: SlopMeter | undefined; area: string[] } = { data: undefined, area: [] };

vi.mock("@/hooks/useSlopMeter", () => ({
  useSlopMeter: (_repo: string, area: string) => {
    state.area.push(area);
    return { data: state.data, isLoading: false, isError: false };
  },
}));

import { SlopMeterSection } from "../SlopMeterSection";

const READING = { score: 0.47, level: 3, name: "Lasagna", provisional: false, computed_at: "2026-10-26T06:00:00Z" };
const METER: SlopMeter = {
  repo: "acme/web",
  meter: READING,
  areas: [{ area: "Billing", meter: { ...READING, score: 0.71, level: 4, name: "Sloppy Joe", provisional: true } }],
  area: null,
  weeks: [
    { week: "2026-10-05", cost: 400, capacity: 300, slop: 100, lines: 5000, score: 0.25 },
    { week: "2026-10-12", cost: 200, capacity: 500, slop: 0, lines: 5200, score: 0.2 },
    { week: "2026-10-19", cost: 900, capacity: 100, slop: 800, lines: 6100, score: 0.47 },
  ],
  reading: { commits_read: 120, commits_total: 120, computed_at: "2026-10-26T06:00:00Z", error: null },
};

describe("SlopMeterSection", () => {
  it("shows the level, the score and a weekly line of cost, capacity and slop", () => {
    state.data = METER;
    render(<SlopMeterSection repo="acme/web" />);
    const reading = screen.getByTestId("slop-meter-reading");
    expect(reading).toHaveAttribute("data-level", "3");
    expect(reading).toHaveTextContent("Lasagna");
    expect(reading).toHaveTextContent("score 0.47");
    expect(reading).toHaveTextContent("About 47% of this repository's weighted lines landed beyond");
    const lines = screen.getByTestId("slop-meter-chart").querySelectorAll("polyline");
    expect([...lines].map((l) => l.getAttribute("data-series"))).toEqual(["cost", "capacity", "slop"]);
    expect(lines[2].getAttribute("points")?.split(" ")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Slop-O-Meter" })).toHaveAttribute(
      "href",
      "https://github.com/slop-o-meter/slop-o-meter",
    );
  });

  it("reads an area's series when one is picked, and says so while provisional", () => {
    state.data = METER;
    state.area = [];
    render(<SlopMeterSection repo="acme/web" />);
    fireEvent.change(screen.getByTestId("slop-meter-area"), { target: { value: "Billing" } });
    expect(state.area.at(-1)).toBe("Billing");
    const reading = screen.getByTestId("slop-meter-reading");
    expect(reading).toHaveTextContent("Sloppy Joe");
    expect(reading).toHaveTextContent("provisional");
    expect(reading).toHaveTextContent("About 71% of Billing's weighted lines");
  });

  it("says how far reading has come before the first score", () => {
    state.data = { ...METER, meter: null, areas: [], weeks: [], reading: { ...METER.reading, commits_read: 5000, commits_total: 12000, error: "git fetch timed out" } };
    render(<SlopMeterSection repo="acme/web" />);
    expect(screen.getByTestId("slop-meter-pending")).toHaveTextContent(
      "Not scored yet: 5,000 of 12,000 commits read. The last reading failed: git fetch timed out",
    );
  });
});
