import { Fragment } from "react";
import { useLocation } from "react-router";
import { cn } from "@/lib/utils";
import { settingsTab } from "@/lib/settingsTabs";
import { NAV_GROUPS } from "./navGroups";

const breadcrumbMap: Record<string, string> = {
  // "Overview" matches the sidebar label; the competing "Dashboard" name is
  // retired.
  "": "Overview",
  console: "Console",
  jobs: "Jobs",
  "merge-approvals": "Merge approvals",
  issues: "Issues",
  agents: "Agents",
  sandboxes: "Sandboxes",
  pipelines: "Pipelines",
  qa: "QA",
  watcher: "Watcher",
  logs: "Logs",
  settings: "Settings",
  environments: "Environments",
  about: "About",
  admin: "Admin",
  repos: "Repos",
  orgs: "Organizations",
  // Both routes fell back to the app name, so the crumb read "embry0 / embry0".
  credentials: "Credentials",
  providers: "LLM providers",
  skills: "Skills",
};

/** Known static second segments get a proper label; dynamic segments (job
 * ids, repo slugs) keep the raw mono rendering. */
const secondSegmentMap: Record<string, string> = {
  audit: "Audit",
  users: "Users",
  onboard: "Onboard",
  operations: "Operations",
  repos: "Repos",
  runs: "Runs",
  admin: "Admin",
};

/** The pages under one QA repo or run, after the repo or run itself. */
const qaTailMap: Record<string, string> = {
  cache: "Cache",
  flake: "Flake",
  config: "Config",
  affected: "Affected",
};

/** Every sidebar entry's path, with the section it sits in and its own label. */
const NAV_BY_PATH = new Map(
  NAV_GROUPS.flatMap((group) => group.items.map((item) => [item.path, { section: group.label, label: item.label }] as const)),
);

interface Crumb {
  label: string;
  /** An identifier (job id, repo slug): mono, never prose. */
  mono?: boolean;
}

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/**
 * The trail for a path. A sidebar page names its section, never its own
 * title again: the h1 right under the line says that. Below a sidebar page
 * the trail is the parent and the record, with a repo always named in full
 * (`owner/name`), on the repo page and under QA alike.
 */
function crumbsFor(pathname: string, search: string): Crumb[] {
  // A "-" segment marks an app route under a name space (the organization
  // wizard at /orgs/-/new), never a name, so the crumb skips it.
  const segments = pathname
    .split("/")
    .filter((s) => s !== "" && s !== "-")
    .map(decode);

  // Settings is its own section, so its line names the open tab instead of
  // standing empty.
  if (segments.length === 1 && segments[0] === "settings") {
    return [{ label: "Settings" }, { label: settingsTab(new URLSearchParams(search).get("tab")).label }];
  }

  const nav = NAV_BY_PATH.get(`/${segments.join("/")}`);
  if (nav) return nav.section === nav.label ? [] : [{ label: nav.section }];

  const [first = "", second, third, ...rest] = segments;
  const head: Crumb = { label: breadcrumbMap[first] ?? "embry0" };
  if (second === undefined) return [head];

  // /repos/<owner>/<name>: the repo is the last two segments together.
  if (first === "repos" && !secondSegmentMap[second]) {
    return [head, { label: segments.slice(1).join("/"), mono: true }];
  }
  // /qa/repos/<owner%2Fname>[/cache|flake|config|apps/<app>] and
  // /qa/runs/<id>[/affected]: the repo or run, then the page under it.
  if (first === "qa" && (second === "repos" || second === "runs") && third !== undefined) {
    const tail = rest
      .filter((s) => s !== "apps")
      .map((s): Crumb => (qaTailMap[s] ? { label: qaTailMap[s] } : { label: s, mono: true }));
    return [head, { label: third, mono: true }, ...tail];
  }

  // /orgs/<owner>/standards: the owner, then the page under it.
  if (first === "orgs" && third === "standards") {
    return [head, { label: second, mono: true }, { label: "Design standard" }];
  }

  const label = secondSegmentMap[second];
  return [head, label ? { label } : { label: second, mono: true }];
}

/**
 * Where the page sits, as a thin line at the top of the content. The header
 * carries the product name, so the trail starts at the section. Settings,
 * whose section is its own name, names its open tab. A page with no trail
 * keeps the line, empty, so the content starts at the same height on every
 * page.
 */
export function Breadcrumbs() {
  const { pathname, search } = useLocation();
  const crumbs = crumbsFor(pathname, search);
  const lineClass =
    "flex h-9 shrink-0 items-center gap-2 overflow-hidden border-b border-border px-[var(--density-padding)] text-sm text-fg-muted";

  if (crumbs.length === 0) return <div aria-hidden="true" className={lineClass} />;

  return (
    <nav aria-label="Breadcrumb" className={lineClass}>
      {crumbs.map((crumb, i) => {
        const last = i === crumbs.length - 1;
        return (
          <Fragment key={i}>
            {i > 0 && (
              <span aria-hidden="true" className="text-white/20">
                /
              </span>
            )}
            <span
              className={cn(
                "truncate",
                crumb.mono ? "font-mono text-xs" : "font-medium",
                last ? "text-fg-secondary" : "shrink-0",
              )}
            >
              {crumb.label}
            </span>
          </Fragment>
        );
      })}
    </nav>
  );
}
