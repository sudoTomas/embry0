"""Pull and merge request addresses: the forge, host, repository and number one names (#181).

One parser for every place that acts on a pull request from its address: the job review routes (send back, reject
and close), the guild's pull request reads, merge approvals and the healing ledger's open times. It reads an
address's shape, not its host:

- ``https://<host>/<owner>/<repo>/pull/<number>`` is GitHub's, on github.com or a GitHub Enterprise host;
- ``https://<host>/<group>[/<subgroup>…]/<project>/-/merge_requests/<iid>`` is GitLab's, on any GitLab.

Whether this deployment reaches that host is :class:`ForgeHosts`'s answer: github.com, since embry0 has no GitHub
Enterprise setting, and the GitLab at ``GITLAB_URL``. An address on any other host parses, so a refusal can name
its forge, and no client is made for it. The organization the address's owner is registered as is the caller's to
read and compare (:meth:`PullRequestUrl.owner`). Leaf module: imports nothing of embry0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import urlsplit

Forge = Literal["github", "gitlab"]

#: The host embry0 reaches GitHub on (``embry0.execution.github_client.GITHUB_API`` is its API).
GITHUB_HOST: Final = "github.com"
#: GitLab's host when ``GITLAB_URL`` names none: the setting's own default, gitlab.com.
DEFAULT_GITLAB_HOST: Final = "gitlab.com"

#: Hosts that are another name for one the parser reports.
_HOST_ALIASES: Final = {"www.github.com": GITHUB_HOST}
#: One path segment of an owner, group, repository or project: what GitHub and GitLab allow in a path, no more.
_SEGMENT: Final = re.compile(r"[A-Za-z0-9_.-]+")
#: A pull request number or merge request iid: positive, no leading zero, and small enough for a BIGINT.
_NUMBER: Final = re.compile(r"[1-9][0-9]{0,17}")
#: The longest host a refusal shows; a longer one is cut, so an address an agent wrote cannot fill the answer.
SHOWN_HOST_CHARS: Final = 64


def _ascii_host(host: str | None) -> str | None:
    """``host`` lowercase in its ASCII form, an internationalized name as punycode (``xn--…``); None for no host
    or one that has no such form (an empty or overlong label)."""
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _shown(host: str) -> str:
    return host if len(host) <= SHOWN_HOST_CHARS else f"{host[: SHOWN_HOST_CHARS - 3]}..."


@dataclass(frozen=True)
class PullRequestUrl:
    """A pull or merge request, as its address names it.

    - forge: whose address shape it is;
    - host: the address's host, lowercase and ASCII (punycode for an internationalized name), without its port
      (``www.github.com`` reads as ``github.com``);
    - repo: ``owner/name`` on GitHub, ``group[/subgroup…]/project`` on GitLab, as the address spells it;
    - number: GitHub's pull request number, GitLab's merge request iid.
    """

    forge: Forge
    host: str
    repo: str
    number: int

    @property
    def owner(self) -> str:
        """The owner an organization is registered as: the repository's first segment."""
        return self.repo.split("/", 1)[0]


def parse_pr_url(url: str | None) -> PullRequestUrl | None:
    """The pull or merge request ``url`` names; None for anything else.

    An http or https address with a host and no credentials, whose path is one of the two shapes above. Path
    segments after the number (``/files``, ``/diffs``), a trailing slash, a query and a fragment name the same
    request. A segment outside :data:`_SEGMENT`, an empty or dot segment, or a number that is zero, has a leading
    zero or overflows a BIGINT makes the address none.
    """
    text = (url or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        host = _ascii_host(parts.hostname)
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        return None
    if parts.scheme.lower() not in ("https", "http") or not host or has_credentials:
        return None
    host = _HOST_ALIASES.get(host, host)
    segments = parts.path.split("/")
    if segments[0] != "":
        return None
    segments = segments[1:-1] if segments[-1] == "" else segments[1:]
    if not segments or any(s in (".", "..") or not _SEGMENT.fullmatch(s) for s in segments):
        return None
    if len(segments) >= 4 and segments[2] == "pull" and _NUMBER.fullmatch(segments[3]):
        return PullRequestUrl("github", host, f"{segments[0]}/{segments[1]}", int(segments[3]))
    if "-" not in segments:
        return None
    # GitLab: the project's path, then "/-/" and the project's own pages. No group or project path is "-".
    cut = segments.index("-")
    project, rest = segments[:cut], segments[cut + 1 :]
    if len(project) < 2 or len(rest) < 2 or rest[0] != "merge_requests" or not _NUMBER.fullmatch(rest[1]):
        return None
    return PullRequestUrl("gitlab", host, "/".join(project), int(rest[1]))


@dataclass(frozen=True)
class ForgeHosts:
    """The host this deployment reaches each forge on."""

    gitlab: str = DEFAULT_GITLAB_HOST
    github: str = GITHUB_HOST

    @classmethod
    def of(cls, config: Any) -> ForgeHosts:
        """The hosts ``config`` names: its ``gitlab_url``'s host, else gitlab.com, as the GitLab client does."""
        try:
            host = _ascii_host(urlsplit(str(getattr(config, "gitlab_url", "") or "")).hostname)
        except ValueError:
            host = None
        return cls(gitlab=host or DEFAULT_GITLAB_HOST)

    def reaches(self, pr: PullRequestUrl) -> bool:
        """Whether ``pr`` is on the host this deployment reaches its forge on."""
        return pr.host == (self.github if pr.forge == "github" else self.gitlab)

    def name(self, url: str | None) -> str:
        """The forge ``url`` is on, as a refusal names it: ``github`` or ``gitlab`` on the host this deployment
        reaches, ``<forge> at <host>`` on another, the bare host for an address that names no pull or merge
        request, and ``an unreadable address`` for one with no host. A host is shown in ASCII and cut at
        :data:`SHOWN_HOST_CHARS`."""
        pr = parse_pr_url(url)
        if pr is not None:
            return pr.forge if self.reaches(pr) else f"{pr.forge} at {_shown(pr.host)}"
        try:
            host = _ascii_host(urlsplit((url or "").strip()).hostname)
        except ValueError:
            host = None
        return _shown(host) if host else "an unreadable address"
