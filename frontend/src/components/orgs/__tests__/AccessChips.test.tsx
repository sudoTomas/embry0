// What the organization access chips say after a probe: push is never
// proven by the probe (only writing a ref proves it), so after a probe it
// reads "Push unknown" with that reason, not "PUSH?" titled "not probed".
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AccessChips } from "../AccessChips";

const probed = { identity: "bot", owner_kind: "Organization", read: true, hooks: true, push: null, repo_count: 3, sample_repo: null, detail: "" };

describe("AccessChips after a probe", () => {
  it("says push is unknown, and why, when the probe cannot tell", () => {
    render(<AccessChips access={probed} />);
    const push = screen.getByTestId("access-push");
    expect(push).toHaveTextContent(/^Push unknown$/);
    expect(push.querySelector("[title]")?.getAttribute("title")).toMatch(/only writing a ref proves push/i);
    expect(push.querySelector("[title]")?.getAttribute("title")).not.toMatch(/not probed/);
  });

  it("says no push when the probe found none", () => {
    render(<AccessChips access={{ ...probed, push: false }} />);
    expect(screen.getByTestId("access-push")).toHaveTextContent(/^No push$/);
  });
});
