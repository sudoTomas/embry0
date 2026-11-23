# Pull request actions and leads on every forge

Issue #181, part of #159. Line numbers are at `f75556f4`.

## Problem

**Pull request actions work on github.com only.**
- Three copies of a github.com-only pattern read a job's pull request from its address:
  - `GITHUB_PR` (`embry0/api/v1/job_review.py:57`), which the guild's reads import (`embry0/api/v1/world.py:403`);
  - `merge_approvals._PR_URL` and `parse_pr_url` (`embry0/services/merge_approvals.py:202`, `:221`);
  - `healing/forge._PR_URL` (`embry0/services/healing/forge.py:24`).
- So, on a GitLab merge request or a GitHub Enterprise pull request:
  - send-back says the job opened no GitHub pull request (409);
  - reject with `close_pr` answers `closed_pr: false` with no error, and records the rejection as if it had closed nothing on purpose (`job_review.py:367-383`).
- And behind the address, every client is GitHub's. `make_forge_client` builds a `GitHubClient` for a GitLab organization too (`embry0/api/v1/organizations.py:845-853`), and comment dispatch reads the pull request with one (`embry0/services/comment_dispatch.py:89`).

**Leads (#129) work only with GitHub and Linear.**
- The backlog finder calls Linear directly (`embry0/services/leads/loop.py:174-177`), not the tracker registry (`embry0/trackers/base.py:61`).
- The lead client is `None` unless the organization's forge is GitHub (`embry0/api/app.py:2565`).
- The `ci`, `alert`, `flaky` and `review` finders call GitHub-only endpoints.
- Owners must be GitHub logins (`embry0/forge_logins.py:15`).

## Decisions (2026-11-23)

1. **Real GitLab support** for send-back, reject with close, and merge request reads, through one address parser and a client per forge. Anything an action cannot do answers 422 `<action> is not supported for this forge (<forge>)`, never a silent no-op.
2. **Finders declare the forges they support.** A finder enabled for a repository on a forge it does not support is skipped at run time, and the reason is recorded on the leads status. The settings write is not refused: a deployment-wide finder list would otherwise be refused for its GitLab organizations.
3. **Owners become forge-qualified handles**, with the people work of #179.

## Design

### Addresses: `embry0/forge_urls.py` (PR 3)

`parse_pr_url(url)` reads an address's shape, not its host, into a `PullRequestUrl(forge, host, repo, number)`:

| Shape | Forge | `repo` |
|---|---|---|
| `https://<host>/<owner>/<repo>/pull/<n>` | `github` | `owner/repo` |
| `https://<host>/<group>[/<subgroup>…]/<project>/-/merge_requests/<iid>` | `gitlab` | `group[/subgroup…]/project` |

- http or https, a host, no credentials in the address.
- Segments after the number, a trailing slash, a query and a fragment name the same request.
- An empty, dot or percent-encoded segment refuses the address. So does a number that is zero, has a leading zero, or overflows a BIGINT.
- `www.github.com` reads as `github.com`. GitLab's legacy address without `/-/` is not read.

`ForgeHosts.of(config)` says which hosts this deployment reaches:
- github.com, since embry0 has no GitHub Enterprise setting;
- the host of `GITLAB_URL`, else gitlab.com.

An address on another host still parses, so a refusal can name it: `github at ghe.example.test`. No client is made for it. `PullRequestUrl.owner` is the repository's first segment, the owner organizations are registered as. The caller reads the organization and compares its forge with the address's: an owner registered on GitLab holds no credential that reaches a GitHub pull request of the same name, and that is a 409.

### Actions (PR 3, then PR 4)

`job_review.PR_ACTION_FORGES` maps each action to the forges it works on today:

| Action | PR 3 | PR 4 |
|---|---|---|
| sending work back | github | github, gitlab |
| closing a pull request | github | github, gitlab |

- An action on a job's pull request acts only on the job's own repository: its path (compared ignoring case, after `REPO_RENAMES`), on the forge the repository is registered on, at the host this deployment reaches that forge on. Another is a 409, checked before anything is acted on; the same path on another forge is another repository. The developer records a pull request its agent's output names only when it is in the repository the job cloned. The guild's numbers and merge asks read only the job's own repository too.
- On any other forge, or on a host the deployment does not reach, the action answers 422 and changes nothing. The rejection is not recorded either, so a person who wants it recorded rejects again without `close_pr`.
- A job with no pull request is unchanged: send-back is a 409, and reject answers `closed_pr: false`.
- A merged pull request is not closed on any forge (`closed_pr: false`).
- Reads degrade: the guild's numbers (`world._pr_stats`), merge asks and the healing ledger's open times read github.com only, and return nothing for anything else. A merge ask logs `merge_approval_forge_not_supported` for a repository merge approvals cover.

### A client per forge (PR 4)

- `make_forge_client` returns the client of `org.forge`: `GitLabClient` with the deployment's `GITLAB_URL` and CA bundle, or `GitHubClient`.
- A `ForgeOps` protocol holds the pull request operations the actions need, so no route builds a forge path:
  - `pull_request(repo, number)`: state (open, closed or merged), head branch, address, files, additions, deletions, commits and title;
  - `comment(repo, number, body)`;
  - `close(repo, number)`.

  GitHub's implementation wraps today's calls. GitLab's maps them onto `merge_requests/<iid>`: its `source_branch`, `state` (`opened`, `closed`, `merged`), notes, and `state_event: close`.
- Comment dispatch reads the pull request through `ForgeOps`, so send-back starts the same pr-followup job on a merge request's source branch.
- `decisions._pr` shows a merge request's number (`!<iid>`) from the parsed address.
- `PR_ACTION_FORGES` gains `gitlab` for each action whose GitLab path is tested end to end.

### Backlogs and finders (PR 5)

- `IssueTracker.list_backlog()` for GitHub, GitLab and Linear. The intake loop goes over the tracker registry instead of calling Linear.
- Each finder declares its forges:

  | Finder | Forges |
  |---|---|
  | `ticket` | github, gitlab, linear |
  | `ci` | github, gitlab (pipelines and jobs) |
  | `flaky` | github, gitlab |
  | `alert` | github |
  | `review` | github |

- A finder enabled for a repository on a forge it does not support is skipped, and the reason (`finder_forge_not_supported`) is recorded on the leads status.
- The lead client is made per forge, so a GitLab organization gets leads.

### Owners (PR 6, with #179)

Owners are stored as `github:<login>` or `gitlab:<username>`. A bare name means the organization's own forge. The pattern check moves from `forge_logins.GITHUB_LOGIN_RE` to one per forge. This depends on #179's people design, so it lands with or after #179's people PR.

### Other address readers, and why they stay

These read a pull request address too. None of them is one of the three github.com-only copies above:

| Where | What it does | Plan |
|---|---|---|
| `services/unfinished_note.py:37-38`, `services/verification_publish.py:29-30` | a GitHub and a GitLab pattern on any host | already reads both forges; stays |
| `storage/repositories/healing_cases.py:30`, `api/case_allowlist.py:71`, `services/healing/backfill.py:61`, `services/healing/recorder.py:46` | the repository or number of either forge, for the healing ledger and allowlists | forge-neutral already; stays |
| `services/merge_sync/sweep.py:95` | merge sync's GitHub sweep | GitHub-only by function; stays |
| `services/decisions.py:227` | the card's pull request number | PR 4, from the parsed address |
| `mcp_server/server.py:147` | a short `#<n>` label for a tool's answer | display only; stays |
| `workflows/issue_to_pr/nodes/_developer.py:1257` | a github.com address in the developer's output, when its JSON line names none | PR 3 keeps only an address in the job's repository; PR 4 finds a merge request's address too |

## PR plan

| PR | What | Size |
|---|---|---|
| 3 | `forge_urls`; the three copies replaced; actions only on the job's own repository; 422 for actions on another forge; this spec | M |
| 4 | a client per forge, `ForgeOps`, GitLab send-back, close and merge request reads | L |
| 5 | backlogs through the tracker registry; finders declare forges and are skipped with a reason; GitLab `ci` and `flaky` | L |
| 6 | forge-qualified owners | M, after #179's people PR |
