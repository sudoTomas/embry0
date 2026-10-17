import { describe, expect, it } from "vitest";
import { loadFixture, loadRawFixture, type FixtureName } from "./fixtures";

const NAMES: FixtureName[] = ["issue-to-pr", "onboarding", "qa", "legacy-rows"];
const CONTRACT_FIXTURES: FixtureName[] = ["issue-to-pr", "onboarding", "qa"];

describe("job-event fixtures", () => {
  it.each(NAMES)("%s loads as a non-empty list of typed events", (name) => {
    const events = loadFixture(name);
    expect(events.length, `${name} is empty`).toBeGreaterThan(0);
    for (const event of events) {
      expect(typeof event.type, `${name}: event ${JSON.stringify(event)} has no string type`).toBe("string");
    }
    expect(loadRawFixture(name)).toHaveLength(events.length);
  });

  it.each(CONTRACT_FIXTURES)("%s ends with job_finished", (name) => {
    const events = loadFixture(name);
    expect(events[events.length - 1].type, `${name} does not end with job_finished`).toBe("job_finished");
  });
});
