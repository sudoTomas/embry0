// frontend/src/lib/types/__tests__/events.contract.test.ts
import { describe, expect, it } from "vitest";
import contractText from "../../../../../docs/contracts/events.json?raw";
import { EVENT_FIELDS } from "../events";

type Contract = Record<string, { required: string[]; optional: string[] }>;

// Read as text through Vite (`?raw`): the app tsconfig has no Node types.
const contract: Contract = JSON.parse(contractText);

describe("event contract mirror", () => {
  it("declares exactly the backend's event types", () => {
    expect(Object.keys(contract).length).toBeGreaterThan(0);
    expect(Object.keys(EVENT_FIELDS).sort()).toEqual(Object.keys(contract).sort());
  });

  it("declares the same required and optional fields per type", () => {
    for (const [type, fields] of Object.entries(contract)) {
      expect(EVENT_FIELDS[type as keyof typeof EVENT_FIELDS], type).toEqual(fields);
    }
  });
});
