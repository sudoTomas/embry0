# embry0's own git commands in a sandbox

embry0 runs git in a job's sandbox for steps of its own: the clone, a
salvage commit and the rescue push of a developer's branch, the changed-file
diff that scopes review and QA, the QA run's default-branch lookup, a pull
request review's checkout, the verify gate's checkouts of the head and base
commits, and the commit and push of an areas or audit delivery.
They run as the agent user, in the agent's workspace. This page says what
those commands read, what they never read, and what happens when embry0
refuses to run one.

Code: `embry0/execution/sandbox_git.py`. Every git command embry0 runs in a
sandbox is built there, and `tests/unit/execution/test_sandbox_git_guard.py`
refuses a git command written anywhere else in embry0.

## What embry0's git reads

| Source | Read by embry0's git | Read by the agent's own git |
|---|---|---|
| System config, `/etc/gitconfig` (root-owned, from the image) | yes | yes |
| embry0's config, `/run/embry0/git/config` (root-owned, written by embry0) | yes, as the global config | no |
| The agent's `~/.gitconfig` and `~/.config/git/config` | **no** | yes |
| The repository's `.git/config` | yes, after the check below | yes |
| `.git/hooks`, or a `core.hooksPath` | **no**: hooks are off | yes |
| Attributes (`.gitattributes`, `.git/info/attributes`) | yes, but no driver is configured to run | yes |
| A nested repository's own config | **no**: embry0's git never starts inside one | yes |

embry0's config holds the commit identity and, when the job has a git-proxy,
the rewrite of the job's repository to the proxy and the per-sandbox bearer.
embry0 writes it as root, from stdin, when the workspace is set up and again
when a paused job's sandbox is re-enrolled. The agent's `~/.gitconfig` gets
the same rewrite, bearer and identity for the agent's own git, as before.
No bearer is ever on a `docker exec` command line.

Every embry0 git command also runs with these set at command scope, which
outranks every config file:

- `core.hooksPath=/dev/null`, `core.fsmonitor=false`;
- `credential.helper=` (empty, which clears every helper, URL-scoped ones
  included), `core.askPass=`, and `GIT_ASKPASS` set and empty;
- `core.editor`, `sequence.editor`, `core.pager` and `core.attributesFile`
  set to harmless values;
- `commit.gpgSign`, `tag.gpgSign`, `push.gpgSign`, `log.showSignature` and
  `merge.verifySignatures` off;
- submodule recursion off (`submodule.recurse`, `fetch.recurseSubmodules`,
  `push.recurseSubmodules`, `diff.ignoreSubmodules`,
  `status.submoduleSummary`), `submodule.<name>.ignore=all` for every name a
  `.gitmodules` carries in the working tree, the index or HEAD, and
  `--ignore-submodules=all` / `--no-recurse-submodules` on status, fetch and
  push; `git add` leaves every gitlink in the index out of its pathspec;
- `GIT_ALLOW_PROTOCOL=http:https`: no other transport, whatever a config file
  allows;
- `GIT_NO_REPLACE_OBJECTS=1` and `GIT_GRAFT_FILE` set to a path that cannot
  exist: no `refs/replace/*` entry stands one object in for another and no
  graft file rewrites a commit's parents, so every object and commit reads as
  it is stored. A shallow clone's own boundary still applies.

## The repository's own config

Before a step runs in an existing repository, the step checks it, in the same
shell:

- `.git` is a directory, not a symlink and not a `.git` file;
- `.git` has no `commondir`;
- every key in `.git/config`, read without its includes (which runs nothing),
  is in a family of keys that run nothing, or is one of the keys pinned above.

The families allowed are:

| Family | Notes |
|---|---|
| `advice.*`, `color.*`, `column.*`, `i18n.*`, `index.*`, `pack.*`, `feature.*`, `rerere.*`, `gc.*` | |
| `help.*` | except `help.browser` |
| `init.*` | except `init.templateDir` |
| `status.*`, `log.*` | `status.submoduleSummary` and `log.showSignature` are pinned off |
| `pull.*`, `rebase.*`, `branch.*`, `user.*` | |
| `push.default`, `push.autoSetupRemote`, `fetch.prune`, `fetch.pruneTags` | |
| `merge.conflictStyle`, `merge.ff`, `merge.log` | |
| `diff.algorithm`, `diff.renames`, `diff.colorMoved`, `diff.mnemonicPrefix`, `diff.noprefix` | |
| `maintenance.*` | `git maintenance register` writes `maintenance.auto`/`maintenance.strategy` |
| `commit.template`, `blame.ignoreRevsFile`, `core.excludesFile` | name a file to read |
| `checkout.defaultRemote`, `push.followTags`, `safe.directory`, `submodule.active` | values, not programs |
| `http.postBuffer` | a buffer size (not a URL); no other `http.*` key is allowed |
| `core.repositoryFormatVersion`, `core.fileMode`, `core.bare`, `core.logAllRefUpdates`, `core.ignoreCase`, `core.precomposeUnicode`, `core.symlinks`, `core.autocrlf`, `core.eol`, `core.safecrlf`, `core.quotePath`, `core.longpaths`, `core.untrackedCache`, `core.whitespace`, `core.abbrev`, `core.commentChar`, `core.checkStat`, `core.trustctime`, `core.sparseCheckout`, `core.sparseCheckoutCone` | the repository format and the working-tree comparison |
| `remote.<name>.url`, `.pushurl`, `.fetch`, `.tagOpt`, `.prune`, `.pruneTags`, `.mirror`, `.skipDefaultUpdate`, `.promisor`, `.partialclonefilter`, `.gh-resolved` | transports are limited to http and https; `remote.origin.mirror` is pinned off (a mirror remote breaks the push) |
| `submodule.<name>.url`, `submodule.<name>.active` | written by `git submodule init` |
| `extensions.*` | `extensions.worktreeConfig` is allowed — embry0 reads `.git/config.worktree` and checks its keys the same way |
| the pinned keys above, `credential.<url>.helper` | the pin wins |

Anything else refuses the step: an `include` or `includeIf`, a `filter`,
`diff` or `merge` driver, `url.*` rewrites, the whole `lfs.*` family (git-lfs
runs `lfs.extension.<x>.clean/smudge` and `lfs.customtransfer.<x>.path`), other
`http.*` settings, `alias.*`, `remote.<name>.uploadpack` or `receivepack`, a
`credential.<url>` key that is not a helper, and any key embry0 does not know.
The list is data in the module (`sandbox_git.INERT`), and its tests check it
against git 2.39's own list of keys and a list of every key that runs a
program.

## When embry0 refuses a step

The step exits with status 86 and `embry0-git-refused: <reason> ...` on
stderr, and embry0 raises `SandboxGitRefused` (`ERR_SANDBOX_GIT_REFUSED`).
Its message names the keys (never their values) and how to clear them.

| Reason | Cause | What to do |
|---|---|---|
| `keys` | `.git/config` carries the listed keys | Remove them in the workspace (`git config --unset-all <key>`), or stop the setup script that writes them (for example `git lfs install --local`, or a `postinstall` that configures a diff or merge driver) |
| `not_a_repository` | `/workspace/.git` is a symlink, a file or missing | Recreate the sandbox |
| `commondir` | `.git/commondir` points git at another directory | Recreate the sandbox |
| `config_unreadable` | `.git/config` does not parse | Fix or recreate it |
| `config_missing`, `config_not_root_owned`, `config_writable` | embry0's own config is missing, or not root-owned and closed to other users | Re-enroll (resume) or recreate the sandbox; a profile with a read-only root filesystem cannot hold it |
| `git_too_old` | The image's git is older than 2.32 | Use an image with a newer git |

What the operator sees, by step:

| Step (`step` label) | Effect |
|---|---|
| `salvage_commit`, `rescue_push` | The unfinished work is not committed or pushed. The job's failure explains the branch is not on origin and names the refusal. Where the repository's `salvage.enabled` is false, `salvage_commit` only looks at the worktree (`git status --porcelain`) and never commits. |
| `branch_probe` | embry0 cannot tell whether the developer's branch is on origin; it carries on as when the probe fails for any other reason. |
| `verify_gate` | The gate reports `error` ("could not verify"), and its note is the refusal. |
| `changed_files`, `base_branch`, `diff_lines` | Review and QA run unscoped, and the base falls back to `main`, as for any failed diff. |
| `default_branch` | The QA run is not trusted with the repository's shared cache: it gets a per-job volume, and no pre-built image is selected for it ([qa-yaml-reference.md](qa-yaml-reference.md), `cache`). |
| `areas_delivery`, `audit_delivery` | No pull request is opened; the report stays. `map_areas_pr_not_pushed` / `context_audit_pr_not_pushed` carry the refusal. |
| `review_checkout`, `qa_checkout`, `clone` | The review, the QA run or the job's setup fails with the refusal. |
| `configure` | Writing the agent's `~/.gitconfig` failed; the job's setup fails. |

Every refusal is also logged at warning as `sandbox_git_refused` with the
job, the step, the reason and the key names, and counted in
`embry0_sandbox_git_refusals_total{step}` ([metrics.md](ops/metrics.md)).

## What this changes for agents

Nothing for an agent's own git: its `~/.gitconfig`, its repository's config
and hooks (husky, pre-commit, lefthook) and its submodules work as before.
What changes is embry0's own steps: they do not run the repository's hooks
(a salvage commit of unfinished work is no longer held up by a lint hook),
they do not follow settings the agent made globally, and they do not inspect
a submodule's working tree (a submodule whose only change is uncommitted
work inside it does not make the salvage check see the workspace as dirty).

git-lfs: no embry0 image installs or configures it, and the whole `lfs.*`
family is refused, because git-lfs (when the image installs its filter) reads
`lfs.extension.<x>.clean/smudge` and `lfs.customtransfer.<x>.path` from
`.git/config` and runs them. A repository that carries LFS settings in its own
`.git/config` — whether `lfs.*` or `filter.lfs.*` (what `git lfs install
--local` writes) — makes embry0's git steps refuse. LFS configured only
system-wide (`git lfs install --system`) leaves the repository's `.git/config`
clean, so embry0's steps run; the git-proxy refuses LFS transfers regardless.

## Requirements

- git 2.32 or newer in the sandbox image (`GIT_CONFIG_GLOBAL`); every embry0
  image ships 2.39;
- bash;
- a root filesystem embry0 can write `/run/embry0/git` on, as root (the
  default; the image's `/run` must be root-owned and closed to other users).

No image needs rebuilding for any of this: embry0 writes its config at run
time and sends the commands as shell text.
