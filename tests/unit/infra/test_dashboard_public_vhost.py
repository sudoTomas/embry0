"""The public dashboard vhost is configuration, not one deployment's hostname.

The frontend image serves two vhosts. The LAN one (``frontend/nginx.conf``)
is the default_server. The public one is rendered at container start from
``frontend/nginx/dashboard-public.conf.template`` for the hostname in
``DASHBOARD_PUBLIC_HOSTNAME``. It refuses the webhook, telegram and internal
API paths, forwards ``X-Forwarded-Proto: https``, and sends CSP and HSTS.
With the variable unset there is no public vhost at all.

Two tiers:

* static: run the real render script with ``sh``, parse what lands in
  conf.d, and ask which server block nginx would pick for a Host and which
  location for a path. Needs only a POSIX shell.
* nginx: lay the files out as the Dockerfile does, inside the Dockerfile's own
  base image, start it through the stock entrypoint, run ``nginx -t``, and
  send real requests. Skipped when no Docker daemon is reachable.

A tree without the render script (the state these tests were written
against) starts on its static config alone, so the tests then fail on what
that config does to a request rather than on a missing file.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "frontend"
DOCKERFILE = FRONTEND / "Dockerfile"
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
LAN_CONF = FRONTEND / "nginx.conf"
TEMPLATE = FRONTEND / "nginx" / "dashboard-public.conf.template"
RENDERER = FRONTEND / "nginx" / "render-dashboard-public.sh"
INDEX_HTML = FRONTEND / "index.html"

VAR = "DASHBOARD_PUBLIC_HOSTNAME"
# Arbitrary names: nothing here may depend on any one deployment's hostname.
PUBLIC_HOST = "dashboard.example.org"
OTHER_HOST = "build-box.example.net"

INGRESS_ONLY_PATHS = [
    "/api/v1/webhook",
    "/api/v1/webhook/github",
    "/api/v1/telegram/update",
    "/api/v1/internal/qa/presign",
]
# Every value the render script must refuse. Shared with the tunnel tests.
INVALID_HOSTNAMES = [
    "_",
    "*",
    "*.example.org",
    ".example.org",
    "~^.*$",
    "https://dashboard.example.org",
    "dashboard.example.org:443",
    "dashboard.example.org/",
    "dashboard.example.org other.example.org",
    "dashboard.example.org;",
    "dashboard.example.org\nother.example.org",
    "dashboard.example.org.",
    "dashboard..example.org",
    "-dashboard.example.org",
    "localhost",
    "dashboard_1.example.org",
]
SECURITY_HEADERS = {
    "x-frame-options",
    "x-content-type-options",
    "x-xss-protection",
    "referrer-policy",
    "strict-transport-security",
    "content-security-policy",
}

# ---------------------------------------------------------------------------
# A small nginx config reader: enough for server/location/add_header/map.
# ---------------------------------------------------------------------------


@dataclass
class Directive:
    name: str
    args: list[str]
    block: list[Directive] | None


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            out.append((c, c))
            i += 1
        elif c in "\"'":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            assert j < n, f"unterminated {c} string in nginx config"
            out.append(("word", "".join(buf)))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "{};\"'":
                j += 1
            out.append(("word", text[i:j]))
            i = j
    return out


def parse_nginx(text: str) -> list[Directive]:
    toks = _tokens(text)
    pos = 0

    def block() -> list[Directive]:
        nonlocal pos
        out: list[Directive] = []
        while pos < len(toks):
            if toks[pos][0] == "}":
                pos += 1
                return out
            words: list[str] = []
            while pos < len(toks) and toks[pos][0] == "word":
                words.append(toks[pos][1])
                pos += 1
            assert words and pos < len(toks), f"dangling tokens {words} in nginx config"
            kind = toks[pos][0]
            pos += 1
            if kind == ";":
                out.append(Directive(words[0], words[1:], None))
            elif kind == "{":
                out.append(Directive(words[0], words[1:], block()))
            else:
                raise AssertionError(f"unexpected {kind!r} after {words}")
        return out

    return block()


def _children(d: Directive) -> list[Directive]:
    assert d.block is not None, f"{d.name} has no block"
    return d.block


def _servers(http: list[Directive]) -> list[Directive]:
    return [d for d in http if d.name == "server"]


def _server_names(server: Directive) -> list[str]:
    return [a for d in _children(server) if d.name == "server_name" for a in d.args]


def _is_default(server: Directive) -> bool:
    return any(d.name == "listen" and "default_server" in d.args for d in _children(server))


def _vhost_for(http: list[Directive], host: str) -> Directive:
    """The server block nginx picks for a Host: an exact name, else the default."""
    servers = _servers(http)
    for s in servers:
        if host in _server_names(s):
            return s
    defaults = [s for s in servers if _is_default(s)]
    assert len(defaults) == 1, f"expected exactly one default_server, found {len(defaults)}"
    return defaults[0]


def _location_for(server: Directive, path: str) -> Directive:
    """Longest prefix match. Every location here is a plain prefix; one with a
    modifier (=, ~, ^~) would need nginx's full order, so it fails loudly."""
    best: Directive | None = None
    for d in _children(server):
        if d.name != "location":
            continue
        assert len(d.args) == 1, f"location {d.args} has a modifier; extend _location_for to emulate it"
        if path.startswith(d.args[0]) and (best is None or len(d.args[0]) > len(best.args[0])):
            best = d
    assert best is not None, f"no location matches {path}"
    return best


def _maps(http: list[Directive]) -> dict[str, str]:
    """`map ... $var { default "..."; }` constants, by variable name."""
    out: dict[str, str] = {}
    for d in http:
        if d.name == "map":
            for entry in _children(d):
                if entry.name == "default":
                    out[d.args[1]] = entry.args[0]
    return out


def _headers(block: Directive, maps: dict[str, str]) -> dict[str, str]:
    return {d.args[0].lower(): maps.get(d.args[1], d.args[1]) for d in _children(block) if d.name == "add_header"}


def _csp_directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


# ---------------------------------------------------------------------------
# Static tier: the real render script, run with the host's sh.
# ---------------------------------------------------------------------------


@dataclass
class Rendered:
    returncode: int
    stderr: str
    conf_d: dict[str, str]

    @property
    def http(self) -> list[Directive]:
        """Everything conf.d contributes to the http block, in include order."""
        return [d for name in sorted(self.conf_d) for d in parse_nginx(self.conf_d[name])]


def _render(tmp_path: Path, hostname: str | None) -> Rendered:
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    shutil.copy(LAN_CONF, conf_d / "default.conf")
    env = {"PATH": "/usr/bin:/bin"}
    if hostname is not None:
        env[VAR] = hostname
    returncode, stderr = 0, ""
    if RENDERER.exists():
        proc = subprocess.run(
            ["sh", str(RENDERER), str(TEMPLATE), str(conf_d / "dashboard-public.conf")],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        returncode, stderr = proc.returncode, proc.stderr
    files = {p.name: p.read_text() for p in conf_d.glob("*.conf")}
    return Rendered(returncode, stderr, files)


def _public_vhost(tmp_path: Path, host: str = PUBLIC_HOST) -> tuple[Directive, dict[str, str]]:
    rendered = _render(tmp_path, host)
    assert rendered.returncode == 0, f"rendering for {host} failed: {rendered.stderr}"
    server = _vhost_for(rendered.http, host)
    assert host in _server_names(server), (
        f"Host {host} lands in the {_server_names(server)} vhost, not a vhost of its own: "
        f"the public-vhost hardening does not follow {VAR}"
    )
    return server, _maps(rendered.http)


@pytest.mark.parametrize("hostname", [None, ""], ids=["unset", "empty"])
def test_unset_hostname_leaves_only_the_lan_vhost(tmp_path: Path, hostname: str | None) -> None:
    rendered = _render(tmp_path, hostname)
    assert rendered.returncode == 0, f"an unset {VAR} must not stop the container: {rendered.stderr}"
    names = [_server_names(s) for s in _servers(rendered.http)]
    assert names == [["_"]], (
        f"with {VAR} unset the only vhost must be the LAN default_server, found server_name sets {names}"
    )
    assert _is_default(_servers(rendered.http)[0])


def test_unset_hostname_removes_a_previously_rendered_vhost(tmp_path: Path) -> None:
    assert RENDERER.exists(), "no render script, so nothing removes a public vhost once the variable is unset"
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    stale = conf_d / "dashboard-public.conf"
    stale.write_text("server { listen 80; server_name stale.example.org; }\n")
    proc = subprocess.run(
        ["sh", str(RENDERER), str(TEMPLATE), str(stale)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert not stale.exists(), "a restart with the variable unset kept the old public vhost"


@pytest.mark.parametrize("host", [PUBLIC_HOST, "e0.ops.example.net"])
def test_the_public_hostname_gets_its_own_vhost(tmp_path: Path, host: str) -> None:
    server, _ = _public_vhost(tmp_path, host)
    assert _server_names(server) == [host]
    assert not _is_default(server), "the public vhost must never be the default_server"


def test_other_hosts_still_get_the_lan_vhost(tmp_path: Path) -> None:
    rendered = _render(tmp_path, PUBLIC_HOST)
    server = _vhost_for(rendered.http, OTHER_HOST)
    assert _server_names(server) == ["_"] and _is_default(server)


@pytest.mark.parametrize("path", INGRESS_ONLY_PATHS)
def test_the_public_vhost_refuses_ingress_only_paths(tmp_path: Path, path: str) -> None:
    server, _ = _public_vhost(tmp_path)
    location = _location_for(server, path)
    returns = [d.args for d in _children(location) if d.name == "return"]
    assert returns == [["404"]], f"{path} on the public vhost hits location {location.args} without a 404"


def test_the_public_vhost_forwards_https_as_the_scheme(tmp_path: Path) -> None:
    server, _ = _public_vhost(tmp_path)
    protos = {
        loc.args[0]: d.args[1]
        for loc in _children(server)
        if loc.name == "location"
        for d in _children(loc)
        if d.name == "proxy_set_header" and d.args[0].lower() == "x-forwarded-proto"
    }
    assert {"/api/", "/ws/", "/mcp"} <= set(protos), f"X-Forwarded-Proto is not set on {protos}"
    assert set(protos.values()) == {"https"}, f"public vhost forwards a non-https scheme: {protos}"


def test_every_header_group_on_the_public_vhost_sends_the_security_headers(tmp_path: Path) -> None:
    """nginx drops server-level add_header in any location that declares its
    own, so each such location must repeat the whole set."""
    server, maps = _public_vhost(tmp_path)
    groups = {"server": _headers(server, maps)}
    for loc in _children(server):
        if loc.name == "location" and any(d.name == "add_header" for d in _children(loc)):
            groups[f"location {loc.args[0]}"] = _headers(loc, maps)
    for where, headers in groups.items():
        missing = SECURITY_HEADERS - set(headers)
        assert not missing, f"{where} on the public vhost does not send {sorted(missing)}"
    assert len({h["content-security-policy"] for h in groups.values()}) == 1, "header groups disagree on the CSP"
    assert len({h["strict-transport-security"] for h in groups.values()}) == 1, "header groups disagree on HSTS"
    hsts = groups["server"]["strict-transport-security"]
    max_age = re.search(r"max-age=(\d+)", hsts)
    assert max_age and int(max_age.group(1)) >= 15552000, f"HSTS max-age under 180 days: {hsts}"


def test_the_csp_locks_scripts_and_framing(tmp_path: Path) -> None:
    server, maps = _public_vhost(tmp_path)
    csp = _csp_directives(_headers(server, maps)["content-security-policy"])
    assert csp.get("default-src") == ["'self'"]
    assert csp.get("script-src") == ["'self'"], f"scripts must be same-origin only: {csp.get('script-src')}"
    assert not [d for d, v in csp.items() if "'unsafe-eval'" in v], "no directive may allow eval"
    assert csp.get("object-src") == ["'none'"]
    assert csp.get("base-uri") == ["'self'"]
    assert csp.get("frame-ancestors") == ["'none'"]
    assert csp.get("form-action") == ["'self'"]
    connect = csp.get("connect-src", [])
    assert "'self'" in connect and f"wss://{PUBLIC_HOST}" in connect, (
        f"connect-src must allow /api, SSE and the job WebSocket on this host: {connect}"
    )
    assert "blob:" in csp.get("img-src", []), "QA artifact thumbnails are blob: URLs"


def test_the_csp_allows_every_external_resource_the_dashboard_source_loads(tmp_path: Path) -> None:
    """index.html scripts and stylesheets, and CSS @imports, from other origins."""
    server, maps = _public_vhost(tmp_path)
    csp = _csp_directives(_headers(server, maps)["content-security-policy"])
    needs: list[tuple[str, str, str]] = []
    for tag in re.findall(r"<(?:link|script)\b[^>]*>", INDEX_HTML.read_text(), re.S):
        url = re.search(r'\b(?:href|src)="(https?://[^/"]+)', tag)
        if url and tag.startswith("<script"):
            needs.append(("index.html", "script-src", url.group(1)))
        elif url and 'rel="stylesheet"' in tag:
            needs.append(("index.html", "style-src", url.group(1)))
    for css in sorted((FRONTEND / "src").rglob("*.css")):
        for origin in re.findall(r"""@import\s+(?:url\()?\s*['"]?(https?://[^/'"\s)]+)""", css.read_text()):
            needs.append((str(css.relative_to(FRONTEND)), "style-src", origin))
    assert needs, "found no external resource; if the dashboard now self-hosts them, tighten the CSP"
    for source, directive, origin in needs:
        assert origin in csp.get(directive, []), f"{source} loads {origin} but {directive} does not allow it"
    if "https://fonts.googleapis.com" in csp.get("style-src", []):
        assert "https://fonts.gstatic.com" in csp.get("font-src", []), "Google Fonts CSS pulls fonts from gstatic"


def test_the_hostname_is_the_only_substitution(tmp_path: Path) -> None:
    rendered = _render(tmp_path, PUBLIC_HOST)
    assert rendered.returncode == 0, rendered.stderr
    assert "dashboard-public.conf" in rendered.conf_d, f"{VAR}={PUBLIC_HOST} rendered no public vhost"
    template = TEMPLATE.read_text()
    assert "${" + VAR + "}" in template
    assert rendered.conf_d["dashboard-public.conf"] == template.replace("${" + VAR + "}", PUBLIC_HOST)


@pytest.mark.parametrize("value", INVALID_HOSTNAMES)
def test_a_value_that_is_not_one_hostname_stops_the_container(tmp_path: Path, value: str) -> None:
    rendered = _render(tmp_path, value)
    assert rendered.returncode != 0, f"{VAR}={value!r} was accepted"
    assert VAR in rendered.stderr, f"the refusal does not name {VAR}: {rendered.stderr!r}"
    assert "dashboard-public.conf" not in rendered.conf_d, "a refused value still left a vhost behind"


def test_the_lan_config_names_no_hostname() -> None:
    servers = _servers(parse_nginx(LAN_CONF.read_text()))
    names = [_server_names(s) for s in servers]
    assert names == [["_"]], (
        f"frontend/nginx.conf must hold only the LAN default_server, found server_name sets {names}; "
        f"a public hostname is configuration ({VAR})"
    )
    assert _is_default(servers[0])


# ---------------------------------------------------------------------------
# Wiring: the image actually runs the script, and compose feeds it.
# ---------------------------------------------------------------------------


def _final_stage_lines() -> list[str]:
    """The final stage's instructions, one per entry, continuations joined."""
    text = re.sub(r"\\\n\s*", " ", DOCKERFILE.read_text())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    start = max(i for i, ln in enumerate(lines) if ln.startswith("FROM "))
    return lines[start:]


def _context_copies() -> dict[str, str]:
    """Final-stage `COPY <src> <dest>` from the build context, dest -> src."""
    copies: dict[str, str] = {}
    for ln in _final_stage_lines():
        m = re.fullmatch(r"COPY\s+(\S+)\s+(\S+)", ln)
        if m and not m.group(1).startswith("--"):
            copies[m.group(2)] = m.group(1)
    return copies


def _script_default(name: str) -> str:
    m = re.search(rf'^{name}="\$\{{\d:-([^}}]+)\}}"', RENDERER.read_text(), re.M)
    assert m, f"render script has no default for {name}"
    return m.group(1)


def test_the_image_runs_the_renderer_before_nginx_starts() -> None:
    copies = _context_copies()
    hooks = [dest for dest, src in copies.items() if src == "nginx/render-dashboard-public.sh"]
    assert hooks, "the Dockerfile never installs the render script, so no public vhost is ever rendered"
    assert len(hooks) == 1 and re.fullmatch(r"/docker-entrypoint\.d/\d+-[\w.-]+\.sh", hooks[0]), (
        f"the stock entrypoint runs only /docker-entrypoint.d/*.sh; the script is installed at {hooks}"
    )
    assert any(ln.startswith("RUN ") and f"chmod 0755 {hooks[0]}" in ln for ln in _final_stage_lines()), (
        "the entrypoint skips a hook without the exec bit"
    )
    assert copies.get(_script_default("template")) == "nginx/dashboard-public.conf.template", (
        "the template is not installed where the render script reads it"
    )
    output = _script_default("output")
    assert re.fullmatch(r"/etc/nginx/conf\.d/[\w.-]+\.conf", output), f"{output} is not included by nginx"
    assert copies.get("/etc/nginx/conf.d/default.conf") == "nginx.conf"
    assert any(
        ln.startswith("RUN ") and "chown -R nginx:nginx" in ln and "/etc/nginx/conf.d" in ln
        for ln in _final_stage_lines()
    ), "conf.d must be writable by the nginx user the image runs as"


def test_compose_feeds_the_hostname_from_interpolation_only() -> None:
    frontend = yaml.safe_load(COMPOSE.read_text())["services"]["frontend"]
    assert frontend.get("environment", {}).get(VAR) == "${" + VAR + ":-}", (
        f"compose must pass {VAR} to the frontend, defaulting to empty (no public vhost)"
    )
    assert "env_file" not in frontend, "an env_file would hand nginx every secret in that file"


# ---------------------------------------------------------------------------
# nginx tier: the base image, the stock entrypoint, real requests.
# ---------------------------------------------------------------------------

_PROBES = [
    (PUBLIC_HOST, "/"),
    (PUBLIC_HOST, "/assets/missing.js"),
    (PUBLIC_HOST, "/api/v1/queue"),
    *[(PUBLIC_HOST, p) for p in INGRESS_ONLY_PATHS],
    (OTHER_HOST, "/"),
    (OTHER_HOST, "/api/v1/webhook"),
    (OTHER_HOST, "/api/v1/maps"),
    (OTHER_HOST, "/api/v1/maps/shop"),
]


@dataclass
class Response:
    status: int
    headers: dict[str, str]


@dataclass
class NginxRun:
    returncode: int
    output: str
    responses: dict[tuple[str, str], Response]


def _base_image() -> str:
    return _final_stage_lines()[0].split()[1]


def _layout_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for dest, src in _context_copies().items():
            path = FRONTEND / src
            info = tar.gettarinfo(str(path), arcname=dest.lstrip("/"))
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            with path.open("rb") as fh:
                tar.addfile(info, fh)
    return buf.getvalue()


def _run_nginx(hostname: str | None) -> NginxRun:
    probes = "\n".join(f"{h} {p}" for h, p in _PROBES)
    script = f"""
tar -x -C /
if ! /docker-entrypoint.sh nginx -t 2>&1; then echo "@@ENTRYPOINT_FAILED"; exit 3; fi
# The image links its logs to stdout; keep them out of the response stream.
rm -f /var/log/nginx/access.log /var/log/nginx/error.log
nginx
printf '%s\\n' "{probes}" | while read -r host path; do
    echo "@@REQ $host $path"
    curl -s -o /dev/null -D - -H "Host: $host" "http://127.0.0.1$path"
done
"""
    env_args = ["-e", f"{VAR}={hostname}"] if hostname is not None else []
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--add-host",
            "orchestrator:127.0.0.1",
            *env_args,
            "--entrypoint",
            "sh",
            _base_image(),
            "-c",
            script,
        ],
        input=_layout_tar(),
        capture_output=True,
        timeout=120,
    )
    output = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
    responses: dict[tuple[str, str], Response] = {}
    for chunk in output.split("@@REQ ")[1:]:
        head, _, rest = chunk.partition("\n")
        host, path = head.split(" ", 1)
        lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        status = int(lines[0].split()[1])
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        responses[(host, path)] = Response(status, headers)
    return NginxRun(proc.returncode, output, responses)


@pytest.fixture(scope="module")
def docker_ok() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    info = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
    if info.returncode != 0:
        pytest.skip("Docker daemon not reachable")


@pytest.fixture(scope="module")
def nginx_public(docker_ok: None) -> NginxRun:
    return _run_nginx(PUBLIC_HOST)


@pytest.fixture(scope="module")
def nginx_unset(docker_ok: None) -> NginxRun:
    return _run_nginx(None)


def _assert_started(run: NginxRun) -> None:
    assert "@@ENTRYPOINT_FAILED" not in run.output and "test is successful" in run.output, (
        f"nginx -t did not pass:\n{run.output}"
    )


@pytest.mark.requires_docker
def test_nginx_accepts_the_rendered_public_vhost(nginx_public: NginxRun) -> None:
    _assert_started(nginx_public)


@pytest.mark.requires_docker
def test_nginx_accepts_the_config_with_the_hostname_unset(nginx_unset: NginxRun) -> None:
    _assert_started(nginx_unset)


@pytest.mark.requires_docker
@pytest.mark.parametrize("path", INGRESS_ONLY_PATHS)
def test_nginx_refuses_ingress_only_paths_on_the_public_host(nginx_public: NginxRun, path: str) -> None:
    _assert_started(nginx_public)
    resp = nginx_public.responses[(PUBLIC_HOST, path)]
    assert resp.status == 404, f"{PUBLIC_HOST}{path} answered {resp.status}, expected the public vhost's 404"
    assert SECURITY_HEADERS <= set(resp.headers), f"404 is missing {SECURITY_HEADERS - set(resp.headers)}"


@pytest.mark.requires_docker
@pytest.mark.parametrize("path", ["/", "/assets/missing.js", "/api/v1/queue"])
def test_nginx_sends_the_security_headers_on_the_public_host(nginx_public: NginxRun, path: str) -> None:
    _assert_started(nginx_public)
    resp = nginx_public.responses[(PUBLIC_HOST, path)]
    missing = SECURITY_HEADERS - set(resp.headers)
    assert not missing, f"{PUBLIC_HOST}{path} ({resp.status}) does not send {sorted(missing)}"
    assert f"wss://{PUBLIC_HOST}" in resp.headers["content-security-policy"]
    expected_cache = {"/": "no-cache", "/assets/missing.js": "immutable"}.get(path)
    if expected_cache:
        assert expected_cache in resp.headers.get("cache-control", ""), resp.headers


@pytest.mark.requires_docker
def test_nginx_keeps_the_lan_vhost_for_other_hosts(nginx_public: NginxRun) -> None:
    _assert_started(nginx_public)
    page = nginx_public.responses[(OTHER_HOST, "/")]
    webhook = nginx_public.responses[(OTHER_HOST, "/api/v1/webhook")]
    assert page.status == 200 and page.headers.get("cache-control") == "no-cache"
    # Proxied to the (absent) orchestrator, not refused: the LAN vhost serves it.
    assert webhook.status == 502, f"LAN vhost answered {webhook.status} for /api/v1/webhook"
    for resp in (page, webhook):
        assert "content-security-policy" not in resp.headers and "strict-transport-security" not in resp.headers


@pytest.mark.requires_docker
def test_nginx_proxies_the_knowledge_map_routes_on_the_lan_vhost(nginx_public: NginxRun) -> None:
    """The maps location takes a larger upload; its list path is proxied too, never redirected."""
    _assert_started(nginx_public)
    for path in ("/api/v1/maps", "/api/v1/maps/shop"):
        resp = nginx_public.responses[(OTHER_HOST, path)]
        assert resp.status == 502, (
            f"LAN vhost answered {resp.status} for {path} (location {resp.headers.get('location')})"
        )


@pytest.mark.requires_docker
def test_nginx_with_the_hostname_unset_serves_every_host_from_the_lan_vhost(nginx_unset: NginxRun) -> None:
    _assert_started(nginx_unset)
    resp = nginx_unset.responses[(PUBLIC_HOST, "/api/v1/webhook")]
    assert resp.status == 502, f"{PUBLIC_HOST}/api/v1/webhook answered {resp.status}"
    assert "content-security-policy" not in resp.headers, "the LAN vhost sent the public CSP"


@pytest.mark.requires_docker
def test_nginx_container_refuses_to_start_on_a_wildcard(docker_ok: None) -> None:
    run = _run_nginx("*.example.org")
    assert run.returncode != 0 and "@@ENTRYPOINT_FAILED" in run.output, (
        f"a wildcard {VAR} did not stop the container:\n{run.output}"
    )
    assert "test is successful" not in run.output and not run.responses
