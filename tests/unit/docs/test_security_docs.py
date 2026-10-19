"""SECURITY.md is the threat model (#525); these tests tie its claims to the code.

Each claim is read from the code it describes (the proxies ProxyManager
launches, the egress ranges it drops, the variables a sandbox's env carries,
the tier of each route, the profile values that keep a sandbox isolated, the
chain that keeps sandboxes apart), so a change on either side that makes the
document false fails here instead of drifting silently.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SECURITY = ROOT / "SECURITY.md"
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"

HOLDS = "### What holds"
DOES_NOT_HOLD = "### What does not hold"


def _section(text: str, heading: str) -> str:
    """The body under ``heading``, up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    assert heading in lines, f"no {heading!r} heading"
    start = lines.index(heading) + 1
    body: list[str] = []
    for line in lines[start:]:
        m = re.match(r"^(#+) ", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)


def _security_section(heading: str) -> str:
    return _section(SECURITY.read_text(), heading)


def _paragraph(section: str, label: str) -> str:
    """The paragraph of ``section`` that opens with ``**<label>**``."""
    for para in section.split("\n\n"):
        if para.startswith(f"**{label}**"):
            return para
    raise AssertionError(f"no paragraph opening with **{label}**")


def _named_after(text: str, lead: str) -> set[str]:
    """The backticked names that follow ``lead`` in ``text``: `a`, or `a` and `b`, up to the next comma."""
    m = re.search(re.escape(lead) + r" (`[^`]+`(?: (?:and|or) `[^`]+`)*)", text)
    assert m, f"no {lead!r} followed by a backticked name"
    return set(re.findall(r"`([^`]+)`", m.group(1)))


def _callers_of(path: Path, callee: str) -> set[str]:
    """Names of the functions in ``path`` whose body calls ``callee`` (as ``x.callee()`` or ``callee()``)."""
    tree = ast.parse(path.read_text())
    callers: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
                if name == callee:
                    callers.add(fn.name)
    return callers


def _github_anchor(heading: str) -> str:
    """The anchor GitHub gives a heading: lowercased, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _launched_proxy_names() -> list[str]:
    """Every ``name=`` ProxyManager passes to ``_launch``, read from the source rather than a constant."""
    tree = ast.parse((ROOT / "embry0" / "execution" / "proxy" / "manager.py").read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_launch":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.append(kw.value.value)
    return names


def test_security_md_names_every_proxy_the_manager_launches() -> None:
    names = _launched_proxy_names()
    assert len(names) >= 8, f"found only {names} launched in manager.py; the AST walk is no longer reading it"
    holds = _security_section(HOLDS)
    missing = [n for n in names if f"`{n}`" not in holds]
    assert not missing, f"SECURITY.md 'What holds' does not name {missing}, which ProxyManager launches"


def test_security_md_lists_every_blocked_egress_range() -> None:
    from embry0.execution.runtime.egress import BLOCKED_PRIVATE_RANGES

    assert BLOCKED_PRIVATE_RANGES, "the egress module blocks no range"
    holds = _security_section(HOLDS)
    missing = [r for r in BLOCKED_PRIVATE_RANGES if f"`{r}`" not in holds]
    assert not missing, f"SECURITY.md 'What holds' does not list the egress drop of {missing}"


def _sandbox_env_llm_keys() -> dict[tuple[str, str], list[str]]:
    """For each LLM provider and credential kind, the env keys a sandbox is created with."""
    from embry0.agents.providers import OPENAI_COMPATIBLE
    from embry0.execution.sandbox_manager import SandboxManager
    from embry0.services.credentials.providers import llm_providers
    from embry0.services.credentials.resolver import ResolvedCredential

    every = {"OPENAI_COMPATIBLE_PROVIDERS": ",".join(p.name for p in OPENAI_COMPATIBLE)}
    keys: dict[tuple[str, str], list[str]] = {}
    for provider, spec in llm_providers(every).items():
        for kind in spec.kinds:
            secret = f"probe-secret-{provider}-{kind}"
            cred = ResolvedCredential(
                id="cred-probe",
                kind=kind,
                provider=provider,
                host=spec.host,
                label="probe",
                secret=secret,
                git_author_name=None,
                git_author_email=None,
                source="registry",
                trace=(),
            )
            env: dict[str, str] = {}
            SandboxManager._inject_llm_env(env, {provider: cred})
            keys[(provider, kind)] = sorted(k for k, v in env.items() if v == secret)
    return keys


def test_security_md_names_each_llm_credential_the_sandbox_env_carries() -> None:
    """The LLM credential is in the sandbox env by decision (ADR 0008); the doc must say which variables."""
    injected = _sandbox_env_llm_keys()
    env_keys = sorted({k for keys in injected.values() for k in keys})
    assert env_keys, f"no LLM credential reaches a sandbox env any more ({injected}); rewrite SECURITY.md and ADR 0008"
    does_not_hold = _security_section(DOES_NOT_HOLD)
    missing = [k for k in env_keys if f"`{k}`" not in does_not_hold]
    assert not missing, (
        f"SECURITY.md 'What does not hold' does not name {missing}, which SandboxManager puts in the sandbox env"
    )


def test_security_md_describes_the_reserved_env_mechanism() -> None:
    from embry0.execution import auth_provider
    from embry0.execution.auth_provider import GUARDED_ENV_KEYS, HOOK_ENV_PREFIXES, env_override_refusal

    holds = _security_section(HOLDS)
    for needle in ("`env_override_refusal`", "`embry0/execution/auth_provider.py`", "`NODE_OPTIONS`"):
        assert needle in holds, f"SECURITY.md 'What holds' does not mention {needle}"
    assert callable(getattr(auth_provider, "env_override_refusal", None)), "the doc names a check that is gone"
    assert "NODE_OPTIONS" in GUARDED_ENV_KEYS, "SECURITY.md says NODE_OPTIONS is value-guarded; it is not any more"
    missing = [p for p in HOOK_ENV_PREFIXES if f"`{p}`" not in holds]
    assert not missing, f"SECURITY.md 'What holds' does not name the reserved namespaces {missing}"

    # The doc says these stay settable, so an operator can point installs at a registry.
    does_not_hold = _security_section(DOES_NOT_HOLD)
    for key in ("npm_config_registry", "PIP_INDEX_URL"):
        assert f"`{key}`" in does_not_hold, f"SECURITY.md 'What does not hold' does not name {key}"
        refusal = env_override_refusal(key, "https://registry.invalid/")
        assert refusal is None, f"SECURITY.md says {key} is operator-settable, but it is now refused: {refusal}"


# (method, route, tier SECURITY.md assigns it, section, what the section must say about it)
_TIER_CLAIMS = [
    ("PUT", "/api/v1/repos/{full_repo:path}/docker-access", "admin", HOLDS, "docker-access"),
    ("GET", "/api/v1/repos/{full_repo:path}/environment/{key}/reveal", "admin", DOES_NOT_HOLD, "admin-only reveal"),
    ("POST", "/api/v1/sandbox-profiles", "operator", HOLDS, "Sandbox profiles are operator tier"),
    ("PUT", "/api/v1/sandbox-profiles/{name}", "operator", HOLDS, "Sandbox profiles are operator tier"),
    ("PUT", "/api/v1/repos/{full_repo:path}/qa-config", "operator", DOES_NOT_HOLD, "`boot_command`"),
    ("PUT", "/api/v1/repos/{full_repo:path}/environment", "operator", DOES_NOT_HOLD, "`npm_config_registry`"),
]


def test_security_md_tier_claims_match_the_routes() -> None:
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency, _auth_dependency
    from embry0.config import Embry0Config
    from tests.fixtures.routes import app_routes

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    tiers: dict[tuple[str, str], str] = {}
    for route in app_routes(app):
        if route.dependant is None:
            continue
        calls = {d.call for d in route.dependant.dependencies}
        tier = "admin" if _admin_dependency in calls else "operator" if _auth_dependency in calls else "open"
        for method in route.methods or ():
            tiers[(method, route.path)] = tier

    problems = []
    for method, path, claimed, heading, needle in _TIER_CLAIMS:
        if needle not in _security_section(heading):
            problems.append(f"SECURITY.md {heading.lstrip('# ')!r} does not mention {needle} ({method} {path})")
        actual = tiers.get((method, path))
        if actual != claimed:
            problems.append(f"SECURITY.md treats {method} {path} as {claimed} tier; the route is {actual}")
    assert not problems, "\n".join(problems)


def test_security_md_names_metrics_among_the_routes_without_auth() -> None:
    """/metrics takes no credential, so 'API authentication' must list it."""
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency, _auth_dependency
    from embry0.config import Embry0Config
    from tests.fixtures.routes import app_routes

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    routes = [route for route in app_routes(app) if route.path == "/metrics"]
    assert routes, "the app serves no /metrics route"
    calls = {d.call for route in routes if route.dependant is not None for d in route.dependant.dependencies}
    assert not calls & {_admin_dependency, _auth_dependency}, "/metrics takes auth now; update SECURITY.md"
    para = _paragraph(_security_section(HOLDS), "API authentication.")
    assert "`/metrics`" in para, "SECURITY.md 'API authentication' leaves out /metrics, which takes no credential"


def test_security_md_states_the_profile_isolation_rules() -> None:
    """'Profile isolation fields' names the check, every field it covers, and the values it allows (#553)."""
    from pydantic import ValidationError

    from embry0.api.schemas import SandboxProfileRequest
    from embry0.execution import profile_isolation as pi

    para = _paragraph(_security_section(HOLDS), "Profile isolation fields.")
    for needle in ("`profile_isolation_problems`", "`embry0/execution/profile_isolation.py`"):
        assert needle in para, f"'Profile isolation fields' does not name {needle}"
    assert callable(getattr(pi, "profile_isolation_problems", None)), "the doc names a check that is gone"
    missing = [f for f in pi.ISOLATION_FIELDS if f"`{f}`" not in para]
    assert not missing, f"'Profile isolation fields' does not name {missing}, which the check covers"

    networks = _named_after(para, "`extra_networks` holds only")
    assert networks == set(pi.ALLOWED_EXTRA_NETWORKS), (
        f"SECURITY.md says extra_networks may hold only {sorted(networks)}; the check allows "
        f"{sorted(pi.ALLOWED_EXTRA_NETWORKS)}"
    )
    opts = _named_after(para, "`security_opt` holds only")
    accepted = {re.split(r"[=:]", v)[0] for v in getattr(pi, "_NO_NEW_PRIVILEGES", ())}
    assert accepted and opts == accepted, (
        f"SECURITY.md says security_opt may hold only {sorted(opts)}; the check accepts spellings of {sorted(accepted)}"
    )
    for opt in ("seccomp=unconfined", "apparmor=unconfined", "label=disable", "no-new-privileges=false"):
        assert pi.security_opt_problem([opt]) is not None, f"security_opt {opt!r} passes; the doc says it is refused"

    assert "`cap_drop` must include `ALL`" in para and "`cap_add` must be empty" in para, "the cap rules are not stated"
    assert pi.cap_drop_problem(["NET_RAW"]) is not None, "a cap_drop without ALL passes; the doc says it is refused"
    assert pi.cap_add_problem(["NET_ADMIN"]) is not None, "a cap_add passes; the doc says it is refused"
    assert "without a registry host" in para, "the base_image rule is not stated"
    assert pi.base_image_problem("registry.invalid/probe:1") is not None, "a registry-qualified base_image passes"

    # "applied at the API and again when a sandbox is created"
    try:
        SandboxProfileRequest(name="probe", cap_add=["SYS_ADMIN"])
    except ValidationError:
        pass
    else:
        raise AssertionError("the profile API accepts cap_add; SECURITY.md says the API refuses it")
    manager = ROOT / "embry0" / "execution" / "sandbox_manager.py"
    assert "_refuse_weakened_isolation" in _callers_of(manager, "profile_isolation_problems"), (
        "SandboxManager no longer applies profile_isolation_problems where the doc says"
    )
    assert "create" in _callers_of(manager, "_refuse_weakened_isolation"), (
        "SandboxManager.create no longer applies the profile isolation check; SECURITY.md says it does"
    )


def _run_chain_write(tmp_path: Path, bridge_nf: str) -> tuple[int, list[str]]:
    """Run the chain-write script under ``sh`` with stub ``ip``/``iptables`` and a stand-in bridge-netfilter flag.

    Returns the exit code and the stub calls, so a check that only appears in the
    script's text (an error message, a comment) cannot pass for the check itself.
    """
    from embry0.execution.runtime import isolation

    script = isolation.build_ensure_script("br-0123456789ab", ["198.51.100.7"])
    flag_path = "/proc/sys/net/bridge/bridge-nf-call-iptables"
    assert flag_path in script, "the chain write no longer reads the bridge-netfilter flag"
    flag = tmp_path / f"bridge-nf-{bridge_nf}"
    flag.write_text(f"{bridge_nf}\n")
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    calls = tmp_path / f"calls-{bridge_nf}"
    for tool in ("ip", "iptables", "iptables-restore"):
        stub = stubs / tool
        stub.write_text(
            f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\n[ "{tool}" = iptables-restore ] && cat >/dev/null\nexit 0\n'
        )
        stub.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", script.replace(flag_path, str(flag))],
        env={**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )
    return result.returncode, calls.read_text().splitlines() if calls.exists() else []


def test_security_md_states_the_sandbox_isolation_chain(tmp_path: Path) -> None:
    """'Egress drops and sandbox isolation' names the chain and says when it is written (#554)."""
    from embry0.execution.runtime import isolation

    para = _paragraph(_security_section(HOLDS), "Egress drops and sandbox isolation.")
    for name in (isolation.CHAIN, isolation.RESTRICTED_NETWORK):
        assert f"`{name}`" in para, f"'Egress drops and sandbox isolation' does not name `{name}`"

    # "drops every new connection between two containers on it unless it is addressed to a running proxy"
    rules = isolation.chain_rules(["198.51.100.7"])
    returns = [r for r in rules if r.endswith("-j RETURN")]
    assert rules[-1].endswith("-j DROP"), f"the chain no longer ends in a DROP: {rules}"
    assert returns == [rules[0], f"-A {isolation.CHAIN} -d 198.51.100.7/32 -j RETURN"] and "ESTABLISHED" in rules[0], (
        f"the chain passes more than replies and the proxies: {rules}"
    )
    # "The write is refused unless the kernel passes bridged traffic to iptables."
    assert "bridged traffic to iptables" in para, "the bridge-netfilter precondition is not stated"
    code, calls = _run_chain_write(tmp_path, "0")
    wrote = [c for c in calls if c.startswith("iptables-restore")]
    assert code == isolation.EXIT_BRIDGE_NETFILTER_OFF and not wrote, (
        f"with bridge netfilter off the chain write exited {code} and wrote {wrote}; SECURITY.md says it is refused"
    )
    code_on, calls_on = _run_chain_write(tmp_path, "1")
    assert code_on != isolation.EXIT_BRIDGE_NETFILTER_OFF and any(c.startswith("iptables-restore") for c in calls_on), (
        f"with bridge netfilter on the chain write still refused ({code_on}); this check cannot tell the two apart"
    )

    # "rewritten from the running proxies at startup, before each sandbox is handed to a job,
    # and when a paused sandbox resumes"
    for needle in ("at startup", "before each sandbox is handed to a job", "when a paused sandbox resumes"):
        assert needle in para, f"'Egress drops and sandbox isolation' does not say the chain is written {needle!r}"
    manager = ROOT / "embry0" / "execution" / "sandbox_manager.py"
    callers = _callers_of(manager, "ensure_isolation")
    missing = {"create", "refresh_proxy_policy"} - callers
    assert not missing, f"SandboxManager.{sorted(missing)} no longer writes the chain; SECURITY.md says it does"
    # A resume and startup write it through refresh_proxy_policy, with the egress exceptions (#568).
    assert "reenroll" in _callers_of(manager, "refresh_proxy_policy"), (
        "SandboxManager.reenroll no longer writes the chain; SECURITY.md says it does"
    )
    assert _callers_of(ROOT / "embry0" / "api" / "app.py", "refresh_proxy_policy"), (
        "startup no longer writes the chain; SECURITY.md says it does"
    )


def test_docs_state_the_ring3_pattern_count() -> None:
    from embry0.safety.patterns import DANGEROUS_BASH_PATTERNS

    count = re.compile(r"\b(\d+) (?:blocked bash patterns|regex patterns|published regular expressions)\b")
    found = []
    for doc in (README, ARCHITECTURE, SECURITY):
        found += [(doc.name, int(n)) for n in count.findall(doc.read_text())]
    assert found, "no doc states the Ring 3 pattern count; this guard would measure nothing"
    wrong = [(name, n) for name, n in found if n != len(DANGEROUS_BASH_PATTERNS)]
    assert not wrong, f"Ring 3 has {len(DANGEROUS_BASH_PATTERNS)} patterns, but the docs say {wrong}"


def test_readme_security_section_agrees_with_security_md() -> None:
    readme = _section(README.read_text(), "## Security model")
    for needle in ("SECURITY.md#what-does-not-hold", "SECURITY.md#reporting-a-vulnerability", "LLM credential"):
        assert needle in readme, f"README's security section does not carry {needle!r}"
    headings = {
        _github_anchor(line.lstrip("#")) for line in SECURITY.read_text().splitlines() if re.match(r"^#+ ", line)
    }
    for anchor in re.findall(r"SECURITY\.md#([\w-]+)", README.read_text()):
        assert anchor in headings, f"README links SECURITY.md#{anchor}, which is no heading of SECURITY.md"


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def test_reporting_channels_are_github_only() -> None:
    """Owner decision (#623): reports go through GitHub, and no address is published."""
    reporting = _security_section("## Reporting a vulnerability")
    assert "security/advisories/new" in reporting, "SECURITY.md has no link to GitHub private vulnerability reporting"
    text = SECURITY.read_text()
    assert "mailto:" not in text, "SECURITY.md publishes a mailto: link; reports go through GitHub only"
    lines = [n for n, line in enumerate(text.splitlines(), 1) if _EMAIL.search(line)]
    assert not lines, f"SECURITY.md publishes an e-mail address on lines {lines}; reports go through GitHub only"


def test_adr_0008_is_indexed() -> None:
    assert (ROOT / "docs" / "adr" / "0008-llm-credential-in-sandbox-env.md").exists(), "ADR 0008 is missing"
    assert "0008-llm-credential-in-sandbox-env.md" in (ROOT / "docs" / "adr" / "README.md").read_text(), (
        "ADR 0008 is not in the ADR index"
    )
