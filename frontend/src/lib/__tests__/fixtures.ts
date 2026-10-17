import issueToPr from "../../../../tests/fixtures/job-events/issue-to-pr.json?raw";
import onboarding from "../../../../tests/fixtures/job-events/onboarding.json?raw";
import qa from "../../../../tests/fixtures/job-events/qa.json?raw";
import legacyRows from "../../../../tests/fixtures/job-events/legacy-rows.json?raw";
import type { LogEvent } from "@/lib/types";
import { normalizeWireEvent } from "@/lib/wire";

const RAW = { "issue-to-pr": issueToPr, onboarding, qa, "legacy-rows": legacyRows } as const;
export type FixtureName = keyof typeof RAW;

/** The fixture as the dashboard sees it: parsed like a REST page and
 * passed through the wire normalizer, so legacy rows arrive in contract shape. */
export function loadFixture(name: FixtureName): LogEvent[] {
  return (JSON.parse(RAW[name]) as unknown[]).map(normalizeWireEvent);
}

/** The fixture as the API sends it (before normalization), for tests of the wire itself. */
export function loadRawFixture(name: FixtureName): unknown[] {
  return JSON.parse(RAW[name]) as unknown[];
}
