"""Which git requests the git-proxy forwards, and to where (#521).

Pure policy: no server. ``git_verdict`` decides one request against one
sandbox's grant; ``GitScope`` turns an enroll body into that grant.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from embry0.execution.proxy.enrollment import EnrollRejected
from embry0.execution.proxy.git_scope import GIT_GRANT_BY_ID, GitGrant, GitScope, git_verdict, normalize_upstream

UPSTREAM = "https://github.com"
READ = GitGrant(upstream=UPSTREAM, repos=frozenset({"acme/widgets"}), push=False)
PUSH = GitGrant(upstream=UPSTREAM, repos=frozenset({"acme/widgets"}), push=True)


@pytest.mark.parametrize(
    ("method", "path", "query", "upstream_url", "service"),
    [
        (
            "GET",
            "/git/acme/widgets.git/info/refs",
            "service=git-upload-pack",
            "https://github.com/acme/widgets.git/info/refs?service=git-upload-pack",
            "git-upload-pack",
        ),
        (
            "POST",
            "/git/acme/widgets.git/git-upload-pack",
            "",
            "https://github.com/acme/widgets.git/git-upload-pack",
            "git-upload-pack",
        ),
        # .git is optional on the way in and always present on the way out.
        (
            "GET",
            "/git/acme/widgets/info/refs",
            "service=git-upload-pack",
            "https://github.com/acme/widgets.git/info/refs?service=git-upload-pack",
            "git-upload-pack",
        ),
        # The scope is case-insensitive, as the forges are; the path keeps its case.
        (
            "POST",
            "/git/Acme/Widgets.git/git-upload-pack",
            "",
            "https://github.com/Acme/Widgets.git/git-upload-pack",
            "git-upload-pack",
        ),
    ],
)
def test_reads_of_the_scoped_repo_are_forwarded(method, path, query, upstream_url, service):
    verdict = git_verdict(method, path, query, READ)
    assert verdict.allowed, verdict.reason
    assert verdict.upstream_url == upstream_url
    assert verdict.service == service
    assert verdict.repo == "acme/widgets"


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("GET", "/git/acme/widgets.git/info/refs", "service=git-receive-pack"),
        ("POST", "/git/acme/widgets.git/git-receive-pack", ""),
    ],
)
def test_receive_pack_needs_the_push_right(method, path, query):
    assert git_verdict(method, path, query, READ).reason == "push_not_allowed"
    verdict = git_verdict(method, path, query, PUSH)
    assert verdict.allowed and verdict.service == "git-receive-pack"


@pytest.mark.parametrize(
    ("method", "path", "query", "reason"),
    [
        # Another repository, however it is spelled.
        ("GET", "/git/acme/other.git/info/refs", "service=git-upload-pack", "repo_out_of_scope"),
        ("GET", "/git/acme/widgets/sub.git/info/refs", "service=git-upload-pack", "repo_out_of_scope"),
        ("GET", "/git/acme/widgets.git.git/info/refs", "service=git-upload-pack", "repo_out_of_scope"),
        ("GET", "/git/acme/info/refs", "service=git-upload-pack", "malformed_path"),
        # Traversal and encodings: nothing to decode, nothing to collapse.
        ("GET", "/git/acme/widgets/../other.git/info/refs", "service=git-upload-pack", "malformed_path"),
        ("GET", "/git/acme/./widgets.git/info/refs", "service=git-upload-pack", "malformed_path"),
        ("GET", "/git/acme//widgets.git/info/refs", "service=git-upload-pack", "malformed_path"),
        ("GET", "/git/acme%2Fwidgets.git/info/refs", "service=git-upload-pack", "malformed_path"),
        ("GET", "/git/acme/%2e%2e/other.git/info/refs", "service=git-upload-pack", "malformed_path"),
        ("GET", "/git/acme\\widgets.git/info/refs", "service=git-upload-pack", "malformed_path"),
        # Only the three smart-HTTP endpoints: no dumb HTTP, no LFS.
        ("GET", "/git/acme/widgets.git/HEAD", "", "path_not_allowed"),
        ("GET", "/git/acme/widgets.git/objects/info/packs", "", "path_not_allowed"),
        ("POST", "/git/acme/widgets.git/info/lfs/objects/batch", "", "path_not_allowed"),
        ("GET", "/repos/acme/widgets", "", "path_not_allowed"),
        # The one query info/refs needs, nothing more.
        ("GET", "/git/acme/widgets.git/info/refs", "", "query_not_allowed"),
        ("GET", "/git/acme/widgets.git/info/refs", "service=git-upload-archive", "query_not_allowed"),
        ("GET", "/git/acme/widgets.git/info/refs", "service=git-upload-pack&x=1", "query_not_allowed"),
        ("POST", "/git/acme/widgets.git/git-upload-pack", "service=git-upload-pack", "query_not_allowed"),
        # Methods.
        ("POST", "/git/acme/widgets.git/info/refs", "service=git-upload-pack", "method_not_allowed"),
        ("GET", "/git/acme/widgets.git/git-upload-pack", "", "method_not_allowed"),
        ("PUT", "/git/acme/widgets.git/git-upload-pack", "", "method_not_allowed"),
    ],
)
def test_everything_else_is_refused_with_a_reason(method, path, query, reason):
    verdict = git_verdict(method, path, query, PUSH)
    assert not verdict.allowed and verdict.upstream_url == ""
    assert verdict.reason == reason


def test_an_unscoped_enrollment_is_refused_everything():
    verdict = git_verdict("GET", "/git/acme/widgets.git/info/refs", "service=git-upload-pack", None)
    assert verdict.reason == "unscoped_enrollment"


def test_an_empty_grant_reaches_no_repository():
    empty = GitGrant(upstream="", repos=frozenset(), push=True)
    verdict = git_verdict("GET", "/git/acme/widgets.git/info/refs", "service=git-upload-pack", empty)
    assert verdict.reason == "repo_out_of_scope"


def test_a_self_managed_upstream_under_a_relative_root():
    grant = GitGrant(upstream="https://git.example.com:8443/gitlab", repos=frozenset({"group/sub/proj"}), push=False)
    verdict = git_verdict("POST", "/git/group/sub/proj.git/git-upload-pack", "", grant)
    assert verdict.upstream_url == "https://git.example.com:8443/gitlab/group/sub/proj.git/git-upload-pack"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com", "https://github.com"),
        ("https://GitHub.com/", "https://github.com"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://git.example.com:8443/gitlab/", "https://git.example.com:8443/gitlab"),
        ("ftp://github.com", None),
        ("https://user:pass@github.com", None),
        ("https://github.com?x=1", None),
        ("https://github.com#frag", None),
        ("https://github.com/a/../b", None),
        ("https://github.com:99999", None),
        ("https://", None),
        ("github.com", None),
        ("", None),
    ],
)
def test_normalize_upstream(value, expected):
    assert normalize_upstream(value) == expected


def _scope() -> tuple[web.Application, GitScope]:
    app = web.Application()
    return app, GitScope(app, proxy_name="git_proxy")


def test_an_enroll_body_becomes_a_grant_and_is_echoed():
    app, scope = _scope()
    grant = scope.parse(
        app, {"allowed_repos": ["Acme/Widgets"], "git_upstream": "https://GitHub.com/", "git_push": True}
    )
    assert grant == GitGrant(upstream="https://github.com", repos=frozenset({"acme/widgets"}), push=True)
    echoed = scope.apply(app, "s1", grant)
    assert echoed == {
        "allowed_repos": ["acme/widgets"],
        "git_upstream": "https://github.com",
        "git_push": True,
        "git_push_branches": [],
    }
    assert app[GIT_GRANT_BY_ID]["s1"] == grant
    scope.forget(app, "s1")
    assert app[GIT_GRANT_BY_ID] == {}


def test_push_defaults_to_false():
    app, scope = _scope()
    grant = scope.parse(app, {"allowed_repos": ["acme/widgets"], "git_upstream": UPSTREAM})
    assert grant is not None and grant.push is False


def test_a_body_without_allowed_repos_is_unscoped():
    app, scope = _scope()
    assert scope.parse(app, {"git_upstream": UPSTREAM, "git_push": True}) is None
    assert scope.apply(app, "s1", None) == {}
    assert "s1" not in app[GIT_GRANT_BY_ID]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"allowed_repos": ["acme/widgets"]}, "no git_upstream"),
        ({"allowed_repos": ["acme/widgets"], "git_upstream": "ftp://x"}, "not an http"),
        ({"allowed_repos": [], "git_upstream": 42}, "not an http"),
        ({"allowed_repos": ["acme/widgets"], "git_upstream": UPSTREAM, "git_push": "yes"}, "true or false"),
        ({"allowed_repos": ["../etc"], "git_upstream": UPSTREAM}, "not a repository path"),
        ({"allowed_repos": "acme/widgets", "git_upstream": UPSTREAM}, "must be a list"),
    ],
)
def test_a_bad_enroll_body_is_refused(body, message):
    app, scope = _scope()
    with pytest.raises(EnrollRejected, match=message):
        scope.parse(app, body)


@pytest.mark.parametrize(
    "path",
    [
        "/git/acme/widgets/../other.git/info/refs",
        "/git/acme/./widgets.git/info/refs",
        "/git/acme%2Fwidgets.git/info/refs",
        "/git/acme/w%69dgets.git/info/refs",
        "/git/acme/widgets?.git/info/refs",
    ],
)
def test_the_upstream_url_is_built_from_plain_segments_whatever_the_scope_normalizer_accepts(monkeypatch, path):
    """The upstream URL is made from the raw segments, so they are checked here, not only by the scope rule.

    The scope normalizer is shared with the forge API proxies (#520). If it
    ever accepted more, a dot segment or an escape would still never reach
    the upstream URL.
    """
    import embry0.execution.proxy.git_scope as git_scope

    monkeypatch.setattr(git_scope, "normalize_git_repo", lambda value: value.lower())
    loose = GitGrant(
        upstream=UPSTREAM, repos=frozenset({"acme/widgets", "acme/../other", "acme%2fwidgets"}), push=False
    )
    verdict = git_scope.git_verdict("GET", path, "service=git-upload-pack", loose)
    assert verdict.reason == "malformed_path", f"{path} reached the upstream URL as {verdict.upstream_url!r}"
