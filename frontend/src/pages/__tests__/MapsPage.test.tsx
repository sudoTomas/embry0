import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MapSummary } from "@/api/maps";

const listed = vi.hoisted(() => ({ maps: [] as MapSummary[] }));
vi.mock("@/hooks/useMaps", () => ({
  useMaps: () => ({ data: listed.maps, isLoading: false, isError: false }),
}));

import { MapsPage } from "../MapsPage";

const BASE = {
  title: "Shop",
  repo: "acme/shop",
  commit: null,
  source: "",
  generated_at: null,
  uploaded_at: null,
  uploaded_by: "",
  nodes: 3,
  edges: 1,
};

beforeEach(() => {
  listed.maps = [];
});

describe("MapsPage: owners (#180)", () => {
  it("shows each map's owner and links it by its owner, so two maps of one slug open apart", () => {
    listed.maps = [
      { ...BASE, slug: "shop", owner: "acme" },
      { ...BASE, slug: "shop", owner: "zeta", title: "Zeta shop", repo: "zeta/shop" },
      { ...BASE, slug: "org", owner: null, title: "Org", repo: null },
    ];
    render(<MemoryRouter><MapsPage /></MemoryRouter>);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href")), "each map links by its owner").toEqual([
      "/maps/shop?owner=acme",
      "/maps/shop?owner=zeta",
      "/maps/org?owner=",
    ]);
    expect(screen.getAllByTestId("map-owner").map((e) => e.textContent)).toEqual([
      "acme · ",
      "zeta · ",
      "no owner (admins only) · ",
    ]);
  });

  it("lists an older backend's maps, which say nothing about owners, as before", () => {
    listed.maps = [{ ...BASE, slug: "shop" }];
    render(<MemoryRouter><MapsPage /></MemoryRouter>);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/maps/shop");
    expect(screen.queryByTestId("map-owner")).toBeNull();
    expect(screen.getByText("acme/shop")).toBeInTheDocument();
  });
});
