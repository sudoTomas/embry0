import type { Forge } from "@/api/repos";

/**
 * The forge's own page for issue `issueNumber` of a job's repository, or null
 * when the forge's address is unknown.
 *
 * The pull request's URL names the forge and its host: GitHub's end in
 * `/pull/<n>`, GitLab's in `/-/merge_requests/<n>`. Without one, a repository
 * registered on GitHub links to github.com. A GitLab host is known only from a
 * merge request URL: an instance can be self-managed, and a guessed host would
 * send the operator to a page that does not exist.
 */
export function forgeIssueUrl(opts: {
  repo: string | null;
  issueNumber: number | null;
  prUrl?: string | null;
  forge?: Forge | null;
}): string | null {
  const { repo, issueNumber, prUrl, forge } = opts;
  if (!repo || issueNumber == null) return null;
  const pr = parseUrl(prUrl);
  if (pr) {
    const gitlab = pr.pathname.match(/^(\/.+)\/-\/merge_requests\/\d+\/?$/);
    if (gitlab) return `${pr.origin}${gitlab[1]}/-/issues/${issueNumber}`;
    const github = pr.pathname.match(/^(\/[^/]+\/[^/]+)\/pull\/\d+\/?$/);
    if (github) return `${pr.origin}${github[1]}/issues/${issueNumber}`;
  }
  if (forge === "github") return `https://github.com/${repo}/issues/${issueNumber}`;
  return null;
}

const TRACKER_NAMES: Record<string, string> = { github: "GitHub", gitlab: "GitLab", linear: "Linear" };

/** A tracker's name as people write it; an issue with no tracker syncs to GitHub. */
export function trackerName(tracker: string | null | undefined): string {
  return TRACKER_NAMES[(tracker ?? "github").toLowerCase()] ?? tracker ?? "GitHub";
}

function parseUrl(raw: string | null | undefined): URL | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}
