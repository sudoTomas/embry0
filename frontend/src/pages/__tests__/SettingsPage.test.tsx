import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";

// The tabs' own panels fetch their settings; this test is about which tab
// is open and where that is kept.
vi.mock("../settings/GeneralTab", () => ({ default: () => <p>general panel</p> }));
vi.mock("../settings/PolicyTab", () => ({ default: () => <p>policy panel</p> }));
vi.mock("../settings/ContextTab", () => ({ default: () => <p>context panel</p> }));
vi.mock("../settings/IntegrationsTab", () => ({ default: () => <p>integrations panel</p> }));
vi.mock("../settings/ApiKeysTab", () => ({ default: () => <p>api keys panel</p> }));
vi.mock("../settings/GameContentTab", () => ({ default: () => <p>game content panel</p> }));

import { SettingsPage } from "../SettingsPage";

function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SettingsPage />
      <Search />
    </MemoryRouter>,
  );
}

describe("SettingsPage", () => {
  it("opens General with no tab in the URL", () => {
    renderAt("/settings");
    expect(screen.getByText("general panel")).toBeInTheDocument();
  });

  it("opens the tab the URL names and keeps a picked tab in the URL", async () => {
    renderAt("/settings?tab=context");
    expect(screen.getByText("context panel")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "API keys" }));
    expect(screen.getByText("api keys panel")).toBeInTheDocument();
    expect(screen.getByTestId("search")).toHaveTextContent("?tab=apikeys");
    await userEvent.click(screen.getByRole("tab", { name: "General" }));
    expect(screen.getByTestId("search")).toBeEmptyDOMElement();
  });

  it("has a Policy tab, second, for the settings document", async () => {
    renderAt("/settings?tab=policy");
    expect(screen.getByText("policy panel")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "General",
      "Policy",
      "Context",
      "Integrations",
      "API keys",
      "Game content",
    ]);
    await userEvent.click(screen.getByRole("tab", { name: "General" }));
    await userEvent.click(screen.getByRole("tab", { name: "Policy" }));
    expect(screen.getByTestId("search")).toHaveTextContent("?tab=policy");
  });

  it("has a Game content tab for the progression content pack (#176)", async () => {
    renderAt("/settings?tab=game");
    expect(screen.queryByRole("tab", { name: "Game content" }), "the tab is listed").not.toBeNull();
    expect(screen.queryByText("game content panel"), "?tab=game opens it").not.toBeNull();
  });
});
