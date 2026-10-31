import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LeadKind } from "@/api/leadKinds";
import type { Lead, Region } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";
import { EMPTY_ART } from "../art/manifest";

vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
}));

import { WorldMap } from "../WorldMap";

const REGION: Region = { repo: "acme/web", jobs: 1, last_job_at: null, owned: true, watched: true, areas: [] };

/** A crack: the scout's find, drawn as its glyph while the art has no sheet for it. */
const CRACK = {
  id: "crack:1",
  kind: "crack",
  repo: "acme/web",
  area: null,
  owners: [],
  title: "A status filter matches one spelling",
  summary: "",
  details: "",
  action: "",
  url: null,
  created_at: "2026-10-30T00:00:00Z",
  level: 25,
  verb: "Mend",
  creature: "crack_pot",
  leader: "developer",
} as Lead;

/** One entry of GET /lead-kinds (the backend's catalogue, embry0/services/lead_kinds.py). */
const KINDS = new Map<string, LeadKind>([
  ["crack", { id: "crack", label: "Crack", plural: "Cracks", about: "a crack the scout found in the code", found_by: "the scout" }],
]);

describe("WorldMap names a quest's kind as the catalogue says (#166)", () => {
  it("labels a creature drawn as its glyph with the kind's label, not its id", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <WorldMap
          art={EMPTY_ART}
          regions={[REGION]}
          quests={[CRACK]}
          parties={[]}
          characters={[]}
          kinds={KINDS}
          onPickLead={() => {}}
          onPickCharacter={() => {}}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("img", { name: "Crack" }), "the map's crack reads as its raw id").toBeInTheDocument();
  });
});
