import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const rows: { current: unknown[] | undefined } = { current: undefined };
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: rows.current }) }));
import { useAgentIdentity, useIdentityLookup } from "../useAgentIdentity";

function Probe({ type }: { type: string }) {
  const id = useAgentIdentity(type);
  return <span data-testid="hook">{`${id.avatar}/${id.color}/${id.displayName}`}</span>;
}
function LookupProbe({ type }: { type: string }) {
  const id = useIdentityLookup()(type);
  return <span data-testid="lookup">{`${id.avatar}/${id.color}/${id.displayName}`}</span>;
}

describe("the reviewer template spelling resolves to the review agent", () => {
  it("falls back to the review builtin when no row is loaded", () => {
    rows.current = undefined;
    render(<><Probe type="reviewer" /><LookupProbe type="reviewer" /></>);
    expect(screen.getByTestId("hook")).toHaveTextContent("warden/rose/Reviewer");
    expect(screen.getByTestId("lookup")).toHaveTextContent("warden/rose/Reviewer");
  });
  it("reads the review row when one is loaded", () => {
    rows.current = [{ type: "review", display_name: "Gatekeeper", avatar: "arbiter", color: "cyan", tagline: "" }];
    render(<><Probe type="reviewer" /><LookupProbe type="reviewer" /></>);
    expect(screen.getByTestId("hook")).toHaveTextContent("arbiter/cyan/Gatekeeper");
    expect(screen.getByTestId("lookup")).toHaveTextContent("arbiter/cyan/Gatekeeper");
  });
});
