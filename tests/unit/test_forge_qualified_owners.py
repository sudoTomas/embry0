"""Owners named with their forge (#181): ``github:<login>``, ``gitlab:<username>``, or bare on the organization's.

Each forge's logins are checked by that forge's own pattern; a bare owner by GitHub's, which GitLab's takes whole, so it
is a login on either forge and reads as the organization's (``owner_people``).
"""

from __future__ import annotations

import pytest

from embry0.forge_logins import GITHUB_LOGIN_RE, GITLAB_LOGIN_RE, owner_text, parse_owner, split_owner
from embry0.services.proposals import CODEOWNERS_PATHS_ON, read_codeowners


@pytest.mark.parametrize(
    ("text", "stored"),
    [
        ("ada", "ada"),
        ("@Ada", "Ada"),
        ("github:octo-cat", "github:octo-cat"),
        ("GitHub:@octo", "github:octo"),
        ("gitlab:bob.smith", "gitlab:bob.smith"),
        ("gitlab:@bob_s", "gitlab:bob_s"),
        (" gitlab:_lead ", "gitlab:_lead"),
    ],
)
def test_an_owner_is_stored_as_written_with_its_forge_lower_cased_and_no_at(text: str, stored: str) -> None:
    assert owner_text(text) == stored


@pytest.mark.parametrize(
    "text",
    [
        "bob_s",  # a GitLab username GitHub would not take: written gitlab:bob_s
        "octo-",
        "a" * 40,
        "github:bob_s",  # GitHub takes no underscore
        "github:-octo",
        "gitlab:-bob",  # GitLab's start with a letter, a digit or an underscore
        "bitbucket:ada",  # a forge embry0 does not read
        "acme/team",
        "octo cat",
        "",
        "gitlab:",
    ],
)
def test_a_name_no_forge_would_take_is_no_owner(text: str) -> None:
    assert parse_owner(text) is None and owner_text(text) is None


def test_a_stored_owner_reads_as_its_named_forge_and_its_login() -> None:
    assert [split_owner(o) for o in ("@Ada", "GitLab:@Bob_S", "bitbucket:x")] == [
        (None, "ada"),
        ("gitlab", "bob_s"),
        (None, "bitbucket:x"),
    ], "read leniently: a forge embry0 does not read is no forge"


def test_every_bare_owner_is_a_login_on_either_forge() -> None:
    """A bare owner reads as whichever forge its organization is on: GitHub's pattern, which checks it, takes no
    login GitLab's would not."""
    alphabet = "aZ09-_."
    for length in range(1, 6):
        for index in range(len(alphabet) ** length):
            word = "".join(alphabet[(index // len(alphabet) ** i) % len(alphabet)] for i in range(length))
            if GITHUB_LOGIN_RE.fullmatch(word):
                assert GITLAB_LOGIN_RE.fullmatch(word), word


# ---- CODEOWNERS, as each forge reads it ----


def test_github_reads_the_last_rule_for_every_file_and_leaves_teams_and_emails_out_with_why() -> None:
    found = read_codeowners("* @old\n/src/ @c\n* @alice @acme/team bob@example.test @Carol @carol\n", "github")
    assert found.owners == ("alice", "Carol"), "as written, each once"
    assert found.skipped == (
        ("@acme/team", "a team: its members are not read"),
        ("bob@example.test", "an email address: no one is matched by email here"),
    )


def test_github_has_no_sections() -> None:
    assert read_codeowners("[Docs]\n* @alice\n", "github").owners == ("alice",)


GITLAB = """\
# rules before the first heading are a section of their own
* @root
docs/ @x

[Docs] @dora
*
^[Ops][2] @ops-default
* @@maintainer @acme/platform/sre @erin_e someone@example.test @-bad
\\[not-a-heading] @z
[docs]
/** @dan.dev
"""


def test_gitlab_reads_each_section_s_last_rule_for_every_file_its_default_owners_and_every_section() -> None:
    found = read_codeowners(GITLAB, "gitlab")
    assert found.owners == ("root", "gitlab:dan.dev", "gitlab:erin_e"), (
        "the default section's, Docs' last rule (its heading met again continues it), the optional Ops section's; "
        "bare where a bare owner may be the login (read on the file's forge), gitlab:<username> where not"
    )
    assert found.skipped == (
        ("@@maintainer", "a role: its members are not read"),
        ("@acme/platform/sre", "a group: its members are not read"),
        ("someone@example.test", "an email address: no one is matched by email here"),
        ("@-bad", "not a gitlab login"),
    )


def test_a_gitlab_section_s_default_owners_own_a_rule_that_names_none() -> None:
    found = read_codeowners("[Docs][2] @dora @dan\n* \n^[Ops] @ops\n/**\n", "gitlab")
    assert found.owners == ("dora", "dan", "ops")


def test_a_name_the_forge_would_not_take_is_left_out_with_why() -> None:
    assert read_codeowners("* @bob_s\n", "github").skipped == (("@bob_s", "not a github login"),)
    assert read_codeowners("* @bob_s\n", "gitlab").owners == ("gitlab:bob_s",)


def test_each_forge_looks_for_codeowners_where_it_does_in_its_order() -> None:
    assert CODEOWNERS_PATHS_ON == {
        "github": (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"),
        "gitlab": ("CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS"),
    }


# ---- where owners are written ----


def test_the_areas_document_takes_an_owner_named_with_its_forge_and_reads_it_back() -> None:
    from embry0.services.areas_document import DocumentInvalid, areas_of, check

    data = check({"areas": [{"name": "Billing", "owners": ["gitlab:@bob_s", "GitHub:Octo", "@pat", "pat"]}]})
    assert data["areas"][0]["owners"] == ["gitlab:bob_s", "github:Octo", "pat"]
    assert areas_of(data)[0].owners == ("gitlab:bob_s", "github:Octo", "pat")
    with pytest.raises(DocumentInvalid) as refused:
        check({"areas": [{"name": "Billing", "owners": ["bob_s"]}]})
    assert [p.type for p in refused.value.problems] == ["invalid_login"], "a bare owner is a GitHub login"


def test_the_feed_is_filtered_by_an_owner_handle() -> None:
    from datetime import UTC, datetime

    from embry0.services.proposals import for_owner, issue_proposal, with_owners

    issue = issue_proposal({"id": "i-1", "repo": "lab/web", "title": "Fix it", "created_at": datetime.now(UTC)})
    stamped = with_owners([issue], {"lab/web": ["gitlab:bob_s", "github:octo", "mira"]})
    on_gitlab = {"lab/web": "gitlab"}
    assert (
        for_owner(stamped, "GitLab:@Bob_S", on_gitlab) == stamped and for_owner(stamped, "@octo", on_gitlab) == stamped
    )
    assert for_owner(stamped, "gitlab:mira", on_gitlab) == stamped, "a bare owner is the organization's forge's"
    assert for_owner(stamped, "mira", on_gitlab) == [], "a bare filter is a GitHub login, as it always was"
    assert for_owner(stamped, "gitlab:octo", on_gitlab) == [] and for_owner(stamped, "mira") == stamped


async def test_a_gitlab_file_is_read_raw_from_the_project_s_default_branch() -> None:
    import httpx

    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.forge_files import read_gitlab_file

    asked: list[str] = []

    def forge(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url).removeprefix("https://gitlab.example.test/api/v4"))
        if request.url.raw_path.decode().split("?")[0].endswith("/projects/lab%2Fweb"):
            return httpx.Response(200, json={"default_branch": "trunk"})
        if b"/repository/files/.gitlab%2FCODEOWNERS/raw" in request.url.raw_path:
            return httpx.Response(200, content=b"* @bob_s\n")
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
    )
    assert await read_gitlab_file(client, "lab/web", ".gitlab/CODEOWNERS") == "* @bob_s\n"
    assert asked[-1] == "/projects/lab%2Fweb/repository/files/.gitlab%2FCODEOWNERS/raw?ref=trunk"
    assert await read_gitlab_file(client, "lab/web", "CODEOWNERS") is None, "a file not there"


async def test_an_empty_gitlab_project_has_no_file_and_its_files_are_not_asked_for() -> None:
    """A project that reads with no default branch (nothing pushed yet) has no file: none, not "could not read"."""
    import httpx

    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.forge_files import read_gitlab_file

    asked: list[str] = []

    def forge(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": 7, "default_branch": None})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
        max_attempts=1,
    )
    assert await read_gitlab_file(client, "lab/web", "CODEOWNERS") is None
    assert not [a for a in asked if "/repository/files/" in a], f"an empty project's file was asked for: {asked}"


@pytest.mark.parametrize("answer", ["project-unread", "project-failed", 403, 500])
async def test_a_gitlab_file_that_could_not_be_read_raises_not_none(answer: object) -> None:
    """ "Could not read" is not "no file": a project that does not read (a 404 for the project, so a 404 for its
    file is not "not there"; a 5xx), or any answer for the file but the file or a 404, raises."""
    import httpx

    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.forge_files import ForgeReadFailed, read_gitlab_file

    def forge(request: httpx.Request) -> httpx.Response:
        if request.url.raw_path.decode().split("?")[0].endswith("/projects/lab%2Fweb"):
            if answer == "project-unread":
                return httpx.Response(404, json={"message": "404 Project Not Found"})
            if answer == "project-failed":
                return httpx.Response(502, json={"message": "502 Bad Gateway"})
            return httpx.Response(200, json={"default_branch": "main"})
        if str(answer).startswith("project-"):
            return httpx.Response(404, json={"message": "404 File Not Found"})
        return httpx.Response(int(str(answer)), json={"message": "no"})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
        max_attempts=1,
    )
    with pytest.raises(ForgeReadFailed):
        await read_gitlab_file(client, "lab/web", "CODEOWNERS")


def test_a_decision_whose_owners_are_unread_says_so_on_offer_and_when_done() -> None:
    """The flag reaches the decision either way it is made, which is what who may answer it from a chat reads."""
    from datetime import UTC, datetime

    from embry0.api.v1.decisions import _with_owners
    from embry0.services.decisions import offered
    from embry0.services.owners import Owners
    from embry0.services.proposals import issue_proposal, with_owners

    issue = issue_proposal({"id": "i-1", "repo": "lab/web", "title": "Fix it", "created_at": datetime.now(UTC)})
    (stamped,) = with_owners([issue], {"lab/web": Owners((), "codeowners", unread=True)})
    assert stamped.to_dict().get("owners_unread") is True
    assert offered(stamped.to_dict(), ask_min=0).to_dict().get("owners_unread") is True
    quest = {"job_id": "job-1", "repo": "lab/web"}
    assert _with_owners(quest, {"job-1": Owners((), "codeowners", unread=True)})["owners_unread"] is True
    assert _with_owners(quest, None)["owners_unread"] is True, "owners not read at all"
    assert "owners_unread" not in _with_owners(quest, {"job-1": Owners(("ada",), "repository")})


def test_a_gitlab_section_that_excludes_every_file_has_no_owners_for_every_file() -> None:
    """GitLab's exclusions (``!``, within their section): one of every file leaves its section no owners for every
    file, whatever its rules; one of some paths leaves every file's owners as they are."""
    found = read_codeowners("[Docs]\n* @alice\n!*\n[Ops]\n/** @ops\n!/docs/\n[Late]\n!/**\n* @late\n", "gitlab")
    assert found.owners == ("ops",), "Docs and Late exclude every file; Ops excludes docs/ alone"
    assert read_codeowners("* @alice\n!*\n", "github").owners == ("alice",), "GitHub reads no line that starts with !"


def test_a_gitlab_heading_is_read_before_a_comment_is_cut() -> None:
    """A section's name may hold a ``#``: the heading is read whole, its own comment cut after it."""
    found = read_codeowners("[Ops] @ops\n*\n[Team #2] @team # the second team\n/docs/ @docs\n", "gitlab")
    assert found.owners == ("ops",), "Team #2 is its own section, with no rule for every file"
    named = read_codeowners("[Team #2] @team # the second team\n*\n", "gitlab")
    assert named.owners == ("team",), "its default owners, the comment left out"


@pytest.mark.parametrize("forge", ["github", "gitlab"])
@pytest.mark.parametrize("pattern", ["**/*", "/**/*"])
def test_every_file_written_with_a_double_star_is_every_file(forge: str, pattern: str) -> None:
    assert read_codeowners(f"{pattern} @alice\n", forge).owners == ("alice",)
