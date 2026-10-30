import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { PolicyLayerLink } from "../PolicyLayerLink";

describe("PolicyLayerLink", () => {
  it("links an organization's and a repository's page to their layer on the Policy tab", () => {
    render(
      <MemoryRouter>
        <PolicyLayerLink layer={{ layer: "organization", owner: "acme" }} />
        <PolicyLayerLink layer={{ layer: "repository", repo: "group/sub/project" }} />
      </MemoryRouter>,
    );
    const [org, repo] = screen.getAllByRole("link", { name: "open it on the Policy tab" });
    expect(org).toHaveAttribute("href", "/settings?tab=policy&owner=acme");
    expect(repo).toHaveAttribute("href", "/settings?tab=policy&repo=group%2Fsub%2Fproject");
    expect(screen.getAllByTestId("policy-layer-link")[0]).toHaveTextContent(
      "The operator policy acme sets over the deployment's is its layer of the settings document",
    );
  });
});
