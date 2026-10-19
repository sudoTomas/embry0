import { describe, it, expect } from "vitest";
import { forgeIssueUrl } from "../forgeLinks";

describe("forgeIssueUrl", () => {
  it("reads a GitHub pull request's repository and host", () => {
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: 4, prUrl: "https://github.com/acme/app/pull/10" }))
      .toBe("https://github.com/acme/app/issues/4");
  });

  it("reads a GitLab merge request's host and nested group path", () => {
    expect(forgeIssueUrl({
      repo: "group/sub/project", issueNumber: 4, prUrl: "https://gitlab.example.com/group/sub/project/-/merge_requests/2",
    })).toBe("https://gitlab.example.com/group/sub/project/-/issues/4");
  });

  it("falls back to github.com only for a repository registered on GitHub", () => {
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: 4, forge: "github" })).toBe("https://github.com/acme/app/issues/4");
    expect(forgeIssueUrl({ repo: "group/project", issueNumber: 4, forge: "gitlab" })).toBeNull();
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: 4 })).toBeNull();
  });

  it("gives nothing without a repository, an issue number or a readable pull request URL", () => {
    expect(forgeIssueUrl({ repo: null, issueNumber: 4, forge: "github" })).toBeNull();
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: null, forge: "github" })).toBeNull();
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: 4, prUrl: "not a url" })).toBeNull();
    expect(forgeIssueUrl({ repo: "acme/app", issueNumber: 4, prUrl: "javascript:alert(1)" })).toBeNull();
  });
});
