// A map part too deeply nested for the API to send as an object arrives as its JSON text (#431): the panel shows
// it read-only with a note, rather than reading fields of a string.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const part = vi.hoisted(() => ({
  current: {
    map: { slug: "deep-map", owner: "acme", title: "Deep", repo: "acme/widgets" },
    node: '{"id": "part-1", "signals": {"a": {"a": "leaf"}}}' as unknown,
    repo: "acme/widgets",
    removable: false,
    detail: null as unknown,
    marks: [],
    work: null,
    lead: null,
  },
}));
vi.mock("@/hooks/useMaps", () => ({
  usePart: () => ({ data: part.current, isLoading: false, isError: false }),
  useMarkPart: () => ({ mutate: vi.fn(), isPending: false }),
  useSendParts: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { PartPanel } from "../PartPanel";

function renderPanel() {
  return render(
    <MemoryRouter>
      <PartPanel slug="deep-map" owner="acme" nodeId="part-1" by="someone" canSend={false} onClose={() => {}} />
    </MemoryRouter>,
  );
}

describe("PartPanel — a part stored as text (#431)", () => {
  it("shows the part's text read-only with the note", () => {
    renderPanel();
    const shown = screen.getByTestId("stored-as-text");
    expect(shown).toHaveTextContent("This part: Too deeply nested to show; stored as text.");
    expect(shown).toHaveTextContent('"signals"');
  });

  it("shows what the map says of a part as text when only that is too deep", () => {
    part.current = {
      ...part.current,
      node: { id: "part-1", label: "Part", kind: "file", signals: [] },
      detail: '{"narrative": {"a": {"a": "leaf"}}}',
    };
    renderPanel();
    expect(screen.getByRole("heading", { name: "Part" })).toBeInTheDocument();
    expect(screen.getByTestId("stored-as-text")).toHaveTextContent("What the map says of it: Too deeply nested");
  });
});
