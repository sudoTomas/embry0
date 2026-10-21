import { describe, expect, it } from "vitest";
import { actionLabel } from "../orgs";

// An organization action reads as words, never its raw API name. Watch in
// particular adds a watcher stack that stays off until its LogQL is set and
// it is enabled, so its name says so.
describe("actionLabel", () => {
  it("names the watch action for what it does: a stack that starts disabled", () => {
    expect(actionLabel("watch")).toBe("Add a disabled watcher stack");
  });

  it("names the other actions in words, and leaves an unknown one as it came", () => {
    expect(actionLabel("register")).toBe("Register");
    expect(actionLabel("hook")).toBe("Add webhook");
    expect(actionLabel("onboard")).toBe("Onboard");
    expect(actionLabel("something_new")).toBe("something_new");
  });
});
