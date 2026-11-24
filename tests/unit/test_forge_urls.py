"""Pull and merge request addresses (#181): one parser for every place that acts on a pull request from its address."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from embry0.forge_urls import ForgeHosts, PullRequestUrl, parse_pr_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/widgets/pull/7", ("github", "github.com", "acme/widgets", 7)),
        ("https://www.github.com/acme/widgets/pull/7/", ("github", "github.com", "acme/widgets", 7)),
        ("http://GitHub.com/acme/widgets/pull/7", ("github", "github.com", "acme/widgets", 7)),
        ("  https://github.com/acme/widgets/pull/7\n", ("github", "github.com", "acme/widgets", 7)),
        ("https://github.com/acme/widgets/pull/7/files?w=1#diff-1", ("github", "github.com", "acme/widgets", 7)),
        ("https://github.com/acme/.github/pull/12", ("github", "github.com", "acme/.github", 12)),
        ("https://ghe.example.test/acme/widgets/pull/3", ("github", "ghe.example.test", "acme/widgets", 3)),
        ("https://github.com:8443/acme/widgets/pull/7", ("github", "github.com", "acme/widgets", 7)),
        ("https://bücher.example/acme/widgets/pull/3", ("github", "xn--bcher-kva.example", "acme/widgets", 3)),
        ("https://github.com/acme/-/pull/4", ("github", "github.com", "acme/-", 4)),
        ("https://gitlab.com/acme/widgets/-/merge_requests/5", ("gitlab", "gitlab.com", "acme/widgets", 5)),
        (
            "https://gitlab.example.test:8443/acme/platform/widgets/-/merge_requests/9/diffs",
            ("gitlab", "gitlab.example.test", "acme/platform/widgets", 9),
        ),
        ("https://gitlab.example.test/acme/pull/-/merge_requests/2", ("gitlab", "gitlab.example.test", "acme/pull", 2)),
    ],
)
def test_an_address_names_its_forge_host_repository_and_number(url: str, expected: tuple[str, str, str, int]) -> None:
    pr = parse_pr_url(url)
    assert pr is not None, f"{url!r} names no pull request"
    assert (pr.forge, pr.host, pr.repo, pr.number) == expected, url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "github.com/acme/widgets/pull/7",
        "ftp://github.com/acme/widgets/pull/7",
        "https://user:secret@github.com/acme/widgets/pull/7",
        "https:///acme/widgets/pull/7",
        "https://github.com/acme/widgets/pulls/7",
        "https://github.com/acme/widgets/issues/7",
        "https://github.com/acme/widgets/pull/0",
        "https://github.com/acme/widgets/pull/07",
        "https://github.com/acme/widgets/pull/1234567890123456789",
        "https://github.com/acme/widgets/pull/７",
        "https://github.com/acme/widgets/pull/7x",
        "https://github.com/acme/pull/7",
        "https://github.com/acme//widgets/pull/7",
        "https://github.com/acme/../widgets/pull/7",
        "https://github.com/acme/wid%67ets/pull/7",
        "https://github.com/acme/widgets/pull/7/../../other/pull/8",
        "https://gitlab.com/widgets/-/merge_requests/5",
        "https://gitlab.com/acme/widgets/merge_requests/5",
        "https://gitlab.com/acme/widgets/-/issues/5",
        "https://gitlab.com/acme/widgets/-/merge_requests",
        "https://gitlab.com/acme/widgets/-/merge_requests/0",
        "https://[::1/acme/widgets/pull/7",
    ],
)
def test_anything_else_names_none(url: str | None) -> None:
    assert parse_pr_url(url) is None, f"{url!r} was read as a pull request"


def test_the_owner_is_the_repository_s_first_segment() -> None:
    assert PullRequestUrl("gitlab", "gitlab.com", "acme/platform/widgets", 1).owner == "acme"
    assert PullRequestUrl("github", "github.com", "acme/widgets", 1).owner == "acme"


def test_the_deployment_reaches_github_com_and_its_own_gitlab() -> None:
    hosts = ForgeHosts.of(SimpleNamespace(gitlab_url="https://GitLab.Example.Test/"))
    reached = {
        url: hosts.reaches(pr)
        for url in (
            "https://github.com/acme/widgets/pull/7",
            "https://ghe.example.test/acme/widgets/pull/7",
            "https://gitlab.example.test/acme/widgets/-/merge_requests/5",
            "https://gitlab.com/acme/widgets/-/merge_requests/5",
            "https://github.com/acme/widgets/-/merge_requests/5",
        )
        if (pr := parse_pr_url(url)) is not None
    }
    assert reached == {
        "https://github.com/acme/widgets/pull/7": True,
        "https://ghe.example.test/acme/widgets/pull/7": False,
        "https://gitlab.example.test/acme/widgets/-/merge_requests/5": True,
        "https://gitlab.com/acme/widgets/-/merge_requests/5": False,
        "https://github.com/acme/widgets/-/merge_requests/5": False,
    }


@pytest.mark.parametrize(
    "config", [None, SimpleNamespace(), SimpleNamespace(gitlab_url=""), SimpleNamespace(gitlab_url="not a url")]
)
def test_without_a_gitlab_url_the_deployment_s_gitlab_is_gitlab_com(config: object) -> None:
    assert ForgeHosts.of(config) == ForgeHosts(gitlab="gitlab.com", github="github.com")


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/acme/widgets/pull/7", "github"),
        ("https://gitlab.com/acme/widgets/-/merge_requests/5", "gitlab"),
        ("https://ghe.example.test/acme/widgets/pull/7", "github at ghe.example.test"),
        ("https://gitlab.example.test/acme/widgets/-/merge_requests/5", "gitlab at gitlab.example.test"),
        ("https://code.example.test/acme/widgets/changes/5", "code.example.test"),
        ("https://bücher.example/acme/widgets/pull/7", "github at xn--bcher-kva.example"),
        (f"https://{'a' * 60}.{'b' * 60}.example/acme/widgets/pull/7", f"github at {'a' * 60}.{'.' * 3}"),
        (f"https://{'a' * 70}.example/acme/widgets/pull/7", "an unreadable address"),
        ("not an address", "an unreadable address"),
        (None, "an unreadable address"),
    ],
)
def test_a_refusal_names_the_forge_and_any_host_the_deployment_does_not_reach(url: str | None, name: str) -> None:
    assert ForgeHosts().name(url) == name


def test_a_full_width_host_is_the_host_it_reads_as() -> None:
    """A decision, not an accident: IDNA maps full-width letters to ASCII, as a browser does, so ``ｇｉｔｈｕｂ.com``
    is github.com, the host the browser would open. The repository and number are still the address's own."""
    pr = parse_pr_url("https://ｇｉｔｈｕｂ.com/acme/widgets/pull/7")
    assert pr == PullRequestUrl("github", "github.com", "acme/widgets", 7)
    assert ForgeHosts().reaches(pr)
