"""Every write the GitLab client sends carries words, never a quick action: notes, diff discussions and
merge request descriptions, however the write is spelled."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, unquote_plus

import httpx
import pytest

from embry0.execution.forge_client import ForgeClient
from embry0.execution.gitlab_client import GitLabClient

ROOT = Path(__file__).resolve().parents[3]
#: A note whose words would merge the merge request and approve it, were GitLab to run them.
BODY = "<!-- marker -->\nLooks good.\n/approve\n  /merge\n\r/close"
NEUTRAL = "<!-- marker -->\nLooks good.\n\u200b/approve\n  \u200b/merge\n\r\u200b/close"


def _client(existing_note: bool = False) -> tuple[GitLabClient, list[tuple[str, Any]]]:
    sent: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            notes = [{"id": 5, "body": "<!-- marker --> old"}] if existing_note else []
            return httpx.Response(200, json=notes)
        sent.append((request.method, json.loads(request.content)))
        return httpx.Response(201, json={"id": 6})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitLabClient(default_token="key-000000000001", http_client=http), sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "existing_note"),
    [
        (lambda c: c.create_issue_note("acme/web", 1, BODY), False),
        (lambda c: c.upsert_sticky_issue_note("acme/web", 1, BODY, marker="<!-- marker -->"), False),
        (lambda c: c.upsert_sticky_issue_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.upsert_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), False),
        (lambda c: c.upsert_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.edit_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.post_mr_note("acme/web", 1, BODY), False),
        (lambda c: c.create_mr_note("acme/web", 1, BODY), False),
        (lambda c: c.create_mr_discussion("acme/web", 1, body=BODY, position={"new_line": 3}), False),
    ],
    ids=[
        "issue-note",
        "sticky-issue-note-new",
        "sticky-issue-note-edited",
        "sticky-mr-note-new",
        "sticky-mr-note-edited",
        "sticky-mr-note-edit",
        "mr-note",
        "mr-note-bool",
        "mr-discussion",
    ],
)
async def test_every_note_is_written_without_a_quick_action(write: Any, existing_note: bool) -> None:
    client, sent = _client(existing_note)
    await write(client)
    assert [body["body"] for _method, body in sent] == [NEUTRAL]


def _alias(send: Any) -> Any:
    """A send method held under another name, as ``post = client.post`` holds it."""
    return send


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [
        lambda c, p: c.post(p, repo="acme/web", json={"body": BODY}),
        lambda c, p: c.put(p, repo="acme/web", json={"description": BODY}),
        lambda c, p: c.patch(p, repo="acme/web", json={"note": BODY}),
        lambda c, p: c.request("POST", p, repo="acme/web", json={"body": BODY}),
        lambda c, p: getattr(c, "put")(p, repo="acme/web", json={"description": BODY}),
        lambda c, p: _alias(c.post)(p, repo="acme/web", json={"body": BODY}),
        lambda c, p: ForgeClient.post(c, p, repo="acme/web", json={"body": BODY}),
        lambda c, p: c.send_words("POST", p, repo="acme/web", json={"body": BODY}),
    ],
    ids=["post", "put", "patch", "request", "getattr", "alias", "forge-client-post", "send-words"],
)
async def test_every_write_however_spelled_carries_no_quick_action(write: Any) -> None:
    """The client's own ``request`` takes the quick actions out, so a writer added later needs no helper to be safe:
    a path built first, an alias, ``getattr`` or the base class's ``post`` all reach it."""
    client, sent = _client()
    path = client.project_path("acme/web", "/merge_requests/1/notes")
    await write(client, path)
    ((_method, payload),) = sent
    assert list(payload.values()) == [NEUTRAL], payload


def _rack_params(raw_query: str) -> list[tuple[str, str]]:
    """A query string's parameters as GitLab's Rack reads them (``Rack::QueryParser``, its pinned 2.2): split on
    ``&`` and ``;`` with any spaces after, ``+`` a space, the key percent-decoded too, and named as
    ``normalize_params`` names it: without the ``[`` and ``]`` before it and the ``]`` after it (``body[]``, an
    array, is ``body`` too)."""
    pairs = []
    for part in re.split(r"[&;] *", raw_query):
        if part:
            key, _equals, value = part.partition("=")
            named = re.match(r"\A[\[\]]*([^\[\]]+)\]*", unquote_plus(key))
            pairs.append((named.group(1) if named else unquote_plus(key), unquote_plus(value)))
    return pairs


def _query_client() -> tuple[GitLabClient, list[list[tuple[str, str]]]]:
    queries: list[list[tuple[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(_rack_params(request.url.query.decode()))
        return httpx.Response(201, json={"id": 6})

    client = GitLabClient(
        default_token="key-000000000001", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return client, queries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [
        lambda c, p: c.post(p, repo="acme/web", params={"body": BODY, "internal": "false"}),
        lambda c, p: c.post(p, repo="acme/web", params=[("body", BODY), ("internal", "false")]),
        lambda c, p: c.post(p, repo="acme/web", params={"body": BODY, "internal": "false"}.items()),
        lambda c, p: c.post(p, repo="acme/web", params=zip(["body", "internal"], [BODY, "false"], strict=True)),
        lambda c, p: c.post(p, repo="acme/web", params=httpx.QueryParams([("body", BODY), ("internal", "false")])),
        lambda c, p: c.post(p + "?body=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?b%6Fdy=" + quote_plus(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?body=" + quote(BODY) + ";internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?[body]=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?body]=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?]]body=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?%5Bbody%5D=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p + "?body[]=" + quote(BODY) + "&internal=false", repo="acme/web"),
        lambda c, p: c.post(p, repo="acme/web", params={"[body]": BODY, "internal": "false"}),
        lambda c, p: c.post(p, repo="acme/web", params=[("body]", BODY), ("internal", "false")]),
        lambda c, p: c.post(p, repo="acme/web", params=[("body[]", BODY), ("internal", "false")]),
    ],
    ids=[
        "a-mapping",
        "pairs",
        "dict-items",
        "zip",
        "query-params",
        "the-path-s-own-query",
        "an-encoded-key",
        "split-on-a-semicolon",
        "the-path-s-bracketed-key",
        "the-path-s-key-closed-by-a-bracket",
        "the-path-s-key-after-brackets",
        "the-path-s-encoded-brackets",
        "the-path-s-array",
        "a-bracketed-key",
        "a-key-closed-by-a-bracket",
        "an-array",
    ],
)
async def test_words_in_the_query_string_carry_no_quick_action(write: Any) -> None:
    """GitLab takes a note's ``body`` from the query string as well as the body (its API docs' own examples), given
    as parameters of any shape or written into the path, and reads the path's query as Rack does."""
    client, queries = _query_client()
    await write(client, client.project_path("acme/web", "/issues/1/notes"))
    assert queries == [[("body", NEUTRAL), ("internal", "false")]], queries


@pytest.mark.asyncio
async def test_words_after_a_semicolon_in_the_path_carry_no_quick_action() -> None:
    """Rack splits on ``;`` as on ``&``: a ``body`` after one is GitLab's ``body``, the other parameter kept as
    written."""
    client, queries = _query_client()
    path = client.project_path("acme/web", "/issues/1/notes")
    await client.post(f"{path}?per_page=20;body=Looks+good.%0A%2Fapprove;body=x%0A%0D%2Fmerge", repo="acme/web")
    assert queries == [[("per_page", "20"), ("body", "Looks good.\n\u200b/approve"), ("body", "x\n\r\u200b/merge")]], (
        queries
    )


@pytest.mark.asyncio
async def test_a_path_whose_query_carries_no_words_is_sent_as_written() -> None:
    """A page link GitLab wrote (``Link: rel="next"``) goes out as it is."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.raw_path.decode())
        return httpx.Response(200, json=[])

    client = GitLabClient(
        default_token="key-000000000001", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    link = client.project_path("acme/web", "/issues?id=9&order_by=updated_at&page=2&per_page=100&state=opened")
    await client.get(link, repo="acme/web")
    assert sent == ["/api/v4" + link]


#: The calls that send a request with no client between them and the network.
_SENDS = {"post", "put", "patch", "request", "send", "stream", "delete"}


def _annotated_http(annotation: ast.AST | None) -> bool:
    text = ast.unparse(annotation) if annotation is not None else ""
    return "httpx.AsyncClient" in text or "httpx.Client" in text


def _is_http(expr: ast.AST | None, names: set[str]) -> bool:
    """Whether ``expr`` is an httpx client: one built, any object's ``_http``, a name bound to one, or a choice of
    them (``_SHARED or client._http``)."""
    if isinstance(expr, ast.Call):
        return ast.unparse(expr.func) in {"httpx.AsyncClient", "httpx.Client"}
    if isinstance(expr, ast.Attribute):
        return expr.attr == "_http"
    if isinstance(expr, ast.Name):
        return expr.id in names or expr.id == "_http"
    if isinstance(expr, ast.BoolOp):
        return any(_is_http(value, names) for value in expr.values)
    if isinstance(expr, ast.IfExp):
        return _is_http(expr.body, names) or _is_http(expr.orelse, names)
    return False


def _raw_sends(tree: ast.AST) -> dict[int, str]:
    """Each call that sends over an httpx client itself, by line, with the function it is in: ``httpx.post``, a
    client built, bound, annotated or taken as a parameter, any object's ``_http``, an attribute or module name
    one was stored in, or one a function returns (``self._get_client().post``)."""
    attributes: set[str] = set()
    names: set[str] = set()
    factories: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            returns = [inner.value for inner in ast.walk(node) if isinstance(inner, ast.Return)]
            if _annotated_http(node.returns) or any(_is_http(value, set()) for value in returns):
                factories.add(node.name)
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            annotated = isinstance(node, ast.AnnAssign) and _annotated_http(node.annotation)
            if annotated or (node.value is not None and _is_http(node.value, names)):
                attributes |= {target.attr for target in targets if isinstance(target, ast.Attribute)}
                names |= {target.id for target in targets if isinstance(target, ast.Name)}
    found: dict[int, str] = {}
    for function in ast.walk(tree):
        if not isinstance(function, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        arguments = function.args.posonlyargs + function.args.args + function.args.kwonlyargs
        bound = names | {argument.arg for argument in arguments if _annotated_http(argument.annotation)}
        for node in ast.walk(function):
            if isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    if _is_http(item.context_expr, bound) and isinstance(item.optional_vars, ast.Name):
                        bound.add(item.optional_vars.id)
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _SENDS):
                continue
            receiver = node.func.value
            made = receiver.func if isinstance(receiver, ast.Call) else None
            if (
                (isinstance(receiver, ast.Name) and receiver.id == "httpx")
                or _is_http(receiver, bound)
                or (isinstance(receiver, ast.Attribute) and receiver.attr in attributes)
                or (isinstance(made, ast.Name) and made.id in factories)
                or (isinstance(made, ast.Attribute) and made.attr in factories)
            ):
                found.setdefault(node.lineno, function.name)
    return found


_EMBRY0 = "the CLI calls embry0's own API"
#: Every function that sends over an httpx client itself, each with where it sends: none of them is a write to
#: GitLab, which goes through ``GitLabClient`` (its ``request`` takes the quick actions out of the words).
RAW_SENDS = {
    ("embry0/execution/forge_client.py", "request"): "ForgeClient.request: the sender GitLabClient.request hands to",
    ("embry0/execution/proxy/gitlab_proxy.py", "_proxy_handler"): (
        "forwards a sandbox's calls to GitLab, read calls only (forge_scope.gitlab_verdict)"
    ),
    ("embry0/execution/proxy/github_proxy.py", "_proxy_handler"): "forwards a sandbox's calls to GitHub",
    ("embry0/execution/proxy/auth_proxy.py", "_proxy_handler"): "forwards a sandbox's model calls",
    ("embry0/execution/proxy/xai_proxy.py", "_forward"): "forwards a sandbox's model calls to xAI",
    ("embry0/execution/proxy/local_llm_proxy.py", "_forward"): "forwards a sandbox's calls to a local model server",
    ("embry0/execution/xai_device_login.py", "start"): "xAI's device login",
    ("embry0/execution/xai_device_login.py", "poll"): "xAI's device login",
    ("embry0/execution/xai_credential.py", "_post_refresh"): "xAI's token refresh",
    ("embry0/notifications/webhook.py", "emit"): "an operator's outbound event webhook, signed",
    ("embry0/services/background_models.py", "ask_json"): "a model endpoint's chat completions",
    ("embry0/services/hosts.py", "_bearer"): "a host provider's token endpoint",
    ("embry0/services/linear_sync.py", "_graphql"): "Linear's GraphQL API",
    ("embry0/services/linear_dispatch.py", "dispatch_job"): "embry0's own jobs API",
    ("embry0/notifications/slack.py", "post_incoming_webhook"): "a Slack incoming webhook",
    ("embry0/notifications/telegram.py", "send_message"): "Telegram's bot API",
    ("embry0/notifications/telegram.py", "edit_message_answered"): "Telegram's bot API",
    ("embry0/notifications/telegram.py", "register_webhook"): "Telegram's bot API",
    ("embry0/api/v1/telegram.py", "telegram_callback"): "Telegram's bot API",
    ("embry0/api/v1/auth_session.py", "callback"): "the sign-in provider's token endpoint",
    ("embry0/api/v1/qa_artifacts.py", "get_result"): "a GET of a presigned object-storage address",
    ("embry0/workspace_init/http.py", "initialize"): "a GET of the address a workspace is fetched from",
    ("embry0/mcp_server/client.py", "_request"): "embry0's own API",
    ("embry0/cli/__main__.py", "cmd_credentials"): _EMBRY0,
    ("embry0/cli/__main__.py", "run_build_qa_image_via_api"): _EMBRY0,
    ("embry0/cli/__main__.py", "cmd_onboard"): _EMBRY0,
    ("embry0/cli/__main__.py", "cmd_review"): _EMBRY0,
    ("embry0/cli/__main__.py", "cmd_init"): _EMBRY0,
    ("embry0/cli/areas.py", "_send"): _EMBRY0,
    ("embry0/cli/people.py", "_send"): _EMBRY0,
    ("embry0/cli/progression_pack.py", "_send"): _EMBRY0,
    ("embry0/cli/settings.py", "_send"): _EMBRY0,
}


def _all_raw_sends(root: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted((root / "embry0").rglob("*.py")):
        sends = _raw_sends(ast.parse(path.read_text()))
        found |= {(str(path.relative_to(root)), function) for function in sends.values()}
    return found


def test_no_module_sends_over_an_httpx_client_but_those_listed() -> None:
    """``GitLabClient.request`` takes the quick actions out of every write it sends; a send over an httpx client
    itself would not. Every such send in embry0 is listed, with where it goes, and none of them is to GitLab."""
    found = _all_raw_sends(ROOT)
    assert found - RAW_SENDS.keys() == set(), (
        f"a send over an httpx client itself: {sorted(found - RAW_SENDS.keys())}. A write to GitLab goes through "
        "GitLabClient, whose request takes the quick actions out of its words; list any other with where it goes"
    )
    assert RAW_SENDS.keys() - found == set(), f"listed but no longer sending: {sorted(RAW_SENDS.keys() - found)}"


@pytest.mark.parametrize(
    "source",
    [
        "async def probe(client, text):\n"
        "    http = client._http or httpx.AsyncClient()\n"
        "    return await http.post(client.url('/x'), json={'body': text})\n",
        "async def probe(http: httpx.AsyncClient, url, text):\n    return await http.post(url, json={'body': text})\n",
        "async def probe(client, text):\n    return await client._http.post(client.url('/x'), json={'body': text})\n",
        "_SHARED = None\n"
        "async def probe(client, text):\n"
        "    http = _SHARED or client._http\n"
        "    return await http.post(client.url('/x'), json={'body': text})\n",
        "_SHARED = httpx.AsyncClient()\n"
        "async def probe(url, text):\n    return await _SHARED.post(url, json={'body': text})\n",
        "class Sender:\n"
        "    def __init__(self):\n        self._session = httpx.AsyncClient()\n"
        "    async def probe(self, url, text):\n        return await self._session.post(url, json={'body': text})\n",
        "async def probe(client, text):\n"
        "    async with httpx.AsyncClient() as http:\n        return await http.post('/x', json={'body': text})\n",
        "class Writer:\n"
        "    def _get_client(self) -> httpx.AsyncClient:\n        return self._c._http\n"
        "    async def probe(self, url, text):\n        return await self._get_client().post(url, json={'body': text})\n",
        "def _shared():\n    return httpx.AsyncClient()\n"
        "async def probe(url, text):\n    return await _shared().post(url, json={'body': text})\n",
    ],
    ids=[
        "own-http",
        "a-parameter",
        "another-object-s",
        "a-choice",
        "module-level",
        "an-attribute",
        "with",
        "a-factory-method",
        "a-factory",
    ],
)
def test_the_raw_send_check_can_fail(source: str) -> None:
    """Each way the reviewer's probes sent over an httpx client is found."""
    assert list(_raw_sends(ast.parse(source)).values()) == ["probe"]


def _forge_client_classes(root: Path) -> set[tuple[str, str]]:
    """Every class in embry0 that is a forge client (``embry0.execution.forge_client.ForgeClient`` and its
    subclasses, at any depth), as ``(module, name)``: another module's class of the same name is not one."""
    defined: list[tuple[str, str, list[str]]] = []
    for path in sorted((root / "embry0").rglob("*.py")):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                defined.append((module, node.name, [ast.unparse(base).split(".")[-1] for base in node.bases]))
    clients = {("embry0.execution.forge_client", "ForgeClient")}
    grew = True
    while grew:
        grew = False
        for module, name, bases in defined:
            if (module, name) not in clients and any(base in {held for _m, held in clients} for base in bases):
                clients.add((module, name))
                grew = True
    return clients


FORGE_CLIENT_CLASSES = _forge_client_classes(ROOT)


def _forge_client_names(tree: ast.AST, classes: set[tuple[str, str]]) -> set[str]:
    """The names a module holds a forge client class under: imported as itself or another name, or through its
    module (``gh.GitHubClient``). A class of the module itself counts by its own name."""
    by_module: dict[str, set[str]] = {}
    for module, name in classes:
        by_module.setdefault(module, set()).add(name)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(node.name == name for _module, name in classes):
            names.add(node.name)
        if isinstance(node, ast.ImportFrom) and node.module in by_module:
            names |= {alias.asname or alias.name for alias in node.names if alias.name in by_module[node.module]}
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                held = f"{node.module}.{alias.name}"
                names |= {f"{alias.asname or alias.name}.{name}" for name in by_module.get(held, set())}
        if isinstance(node, ast.Import):
            for alias in node.names:
                names |= {f"{alias.asname or alias.name}.{name}" for name in by_module.get(alias.name, set())}
    return names


def _skips(tree: ast.AST, classes: set[tuple[str, str]] | None = None) -> list[str]:
    """Each call that hands a request on past ``GitLabClient.request``: a forge client class's ``request`` called
    unbound (``ForgeClient.request(client, ...)``, ``GitHubClient.request(...)``) under any name,
    ``super(<class>, <object>).request`` anywhere, and ``super().request`` outside a ``request`` override."""
    names = _forge_client_names(tree, FORGE_CLIENT_CLASSES if classes is None else classes)
    found: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "request":
                continue
            receiver = node.func.value
            text = ast.unparse(receiver)
            a_super = isinstance(receiver, ast.Call) and ast.unparse(receiver.func) == "super"
            if (
                text in names
                or (a_super and (receiver.args or receiver.keywords))
                or (a_super and function.name != "request")
            ):
                found.append(f"line {node.lineno} in {function.name}: {ast.unparse(node.func)}")
    return found


def test_nothing_hands_a_request_past_the_gitlab_client_s_own() -> None:
    """``ForgeClient.request(client, ...)`` and ``super().request`` skip ``GitLabClient.request``: no module calls
    them, but the override itself."""
    past = {
        str(path.relative_to(ROOT)): skips
        for path in sorted((ROOT / "embry0").rglob("*.py"))
        if (skips := _skips(ast.parse(path.read_text())))
    }
    assert past == {}, f"calls that skip GitLabClient.request: {past}"


def test_the_forge_client_classes_are_found() -> None:
    assert {
        ("embry0.execution.forge_client", "ForgeClient"),
        ("embry0.execution.github_client", "GitHubClient"),
        ("embry0.execution.gitlab_client", "GitLabClient"),
    } <= FORGE_CLIENT_CLASSES, FORGE_CLIENT_CLASSES
    assert ("embry0.services.org_actions", "ForgeClient") not in FORGE_CLIENT_CLASSES, "a protocol of the same name"


def test_the_skip_check_can_fail() -> None:
    source = (
        "from embry0.execution.forge_client import ForgeClient as _FC\n"
        "from embry0.execution.github_client import GitHubClient as _GHC\n"
        "from embry0.execution.gitlab_client import GitLabClient as _GLC\n"
        "from embry0.execution import github_client as gh\n"
        "async def probe(client, repo, text):\n"
        "    return await _FC.request(client, 'POST', '/x', repo=repo, json={'body': text})\n"
        "async def other(client):\n"
        "    return await _GHC.request(client, 'POST', '/x')\n"
        "async def through(client):\n"
        "    return await gh.GitHubClient.request(client, 'POST', '/x')\n"
        "async def around(client):\n"
        "    return await super(_GLC, client).request('POST', '/x')\n"
        "class C:\n"
        "    async def note(self):\n        return await super().request('POST', '/x')\n"
        "    async def request(self, method, path):\n        return await super().request(method, path)\n"
        "class Sub(_GLC):\n"
        "    async def request(self, method, path):\n        return await super(_GLC, self).request(method, path)\n"
    )
    assert [skip.split(":")[0] for skip in _skips(ast.parse(source))] == [
        "line 6 in probe",
        "line 8 in other",
        "line 10 in through",
        "line 12 in around",
        "line 15 in note",
        "line 20 in request",
    ]


@pytest.mark.asyncio
async def test_send_words_sends_every_words_field_without_quick_actions() -> None:
    """Each field GitLab reads quick actions in, under whatever key, and nothing else."""
    client, sent = _client()
    payload = {"body": BODY, "note": "/approve", "description": "x\n\r/draft", "title": "/kept", "position": {"a": 1}}
    await client.send_words(
        "POST", client.project_path("acme/web", "/merge_requests/1/notes"), repo="acme/web", json=payload
    )
    assert sent == [
        (
            "POST",
            {
                "body": NEUTRAL,
                "note": "\u200b/approve",
                "description": "x\n\r\u200b/draft",
                "title": "/kept",
                "position": {"a": 1},
            },
        )
    ]


@pytest.mark.asyncio
async def test_a_merge_request_s_description_carries_no_quick_action() -> None:
    """The description a merge request opens with carries the developer's own summary: a /target_branch or /draft in
    it would retarget or draft the merge request GitLab is creating."""
    from embry0.services.gitlab_mrs import ensure_mr_for_branch

    created: list[dict[str, Any]] = []

    def forge(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/merge_requests"):
            return httpx.Response(200, json=[])
        if request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "POST" and request.url.path.endswith("/merge_requests"):
            created.append(json.loads(request.content))
            return httpx.Response(
                201, json={"iid": 9, "web_url": "https://gitlab.example.test/acme/web/-/merge_requests/9"}
            )
        return httpx.Response(200, json={})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
    )
    summary = "Fixed the sender.\n/target_branch release-2.0\n/label ~deploy-now\n\r/draft"
    url = await ensure_mr_for_branch(
        repo="acme/web", branch="embry0/fix-sender", title="Fix the sender", body=summary, client=client
    )
    assert url and created, created
    description = created[0]["description"]
    assert not any(line.lstrip(" \t\r").startswith("/") for line in description.replace("\r", "\n").splitlines()), (
        description
    )
    assert created[0]["target_branch"] == "main"
