"""The sandbox images pin npm, and CI gates their libraries on fixable HIGHs (#317).

The sandbox images need Node and npm: agents install, boot and test target
repositories with them. They ran `npm install -g npm@latest`, which moves under
any rebuild; the npm it fetched vendored undici 6.29.0, with three HIGH
advisories, and CI's Trivy gate on these images, CRITICAL only, built them
green. npm is pinned now, and the library gate #305 gave the orchestrator image
(HIGH and CRITICAL, with an allowlist whose entries carry a reason and an
expiry date) scans the sandbox images too.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.infra.test_orchestrator_image_scan import _allowlist_problems, _instructions

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = [ROOT / "infra" / "Dockerfile.sandbox", ROOT / "infra" / "Dockerfile.sandbox.qa"]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ALLOWLIST = ROOT / "infra" / "trivy" / "sandbox.trivyignore.yaml"
#: The images CI builds from the two Dockerfiles, by the tag it gives them.
IMAGES = {"embry0-sandbox:ci": "infra/Dockerfile.sandbox", "embry0-sandbox-qa:latest": "infra/Dockerfile.sandbox.qa"}

_NPM_INSTALL = re.compile(r"\bnpm (?:install|i) (?:-g|--global) npm@(\S+)")


def _npm_pins(dockerfile: Path) -> list[str]:
    return [m.group(1) for ins in _instructions(dockerfile) for m in _NPM_INSTALL.finditer(ins)]


def test_each_sandbox_image_installs_one_exact_npm_release() -> None:
    pins = {path.name: _npm_pins(path) for path in DOCKERFILES}
    assert all(len(found) == 1 for found in pins.values()), f"each sandbox Dockerfile installs npm once: {pins}"
    loose = {name: found for name, found in pins.items() if not re.fullmatch(r"\d+\.\d+\.\d+", found[0])}
    assert not loose, (
        "npm must be an exact release, not a tag or a range: npm@latest moves under every rebuild, and the "
        f"npm it fetched vendored undici 6.29.0 with three HIGH advisories (#317): {loose}"
    )
    assert len({found[0] for found in pins.values()}) == 1, f"the sandbox images install one npm release: {pins}"


def _steps_by_job() -> dict[str, list[dict[str, Any]]]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return {name: list(job.get("steps") or []) for name, job in jobs.items()}


def _builds(step: dict[str, Any], image: str) -> bool:
    """Whether ``step`` builds ``image`` from its Dockerfile onto the runner's daemon, where Trivy can read it."""
    if not str(step.get("uses", "")).startswith("docker/build-push-action@"):
        return False
    given = step.get("with") or {}
    return given.get("tags") == image and given.get("file") == IMAGES[image] and given.get("load") is True


def _scans(vuln_type: str) -> dict[str, list[dict[str, Any]]]:
    """Each sandbox image's Trivy steps of ``vuln_type``, with their job and whether that job built the image first."""
    gates: dict[str, list[dict[str, Any]]] = {}
    for job, steps in _steps_by_job().items():
        for index, step in enumerate(steps):
            given = step.get("with") or {}
            image = given.get("image-ref")
            if not str(step.get("uses", "")).startswith("aquasecurity/trivy-action@") or image not in IMAGES:
                continue
            if given.get("vuln-type", "os,library") != vuln_type:
                continue
            built_before = any(_builds(earlier, image) for earlier in steps[:index])
            gates.setdefault(image, []).append({"job": job, "built_before": built_before, **given})
    return gates


def test_each_sandbox_image_keeps_a_critical_gate_on_the_whole_image() -> None:
    gates = _scans("os,library")
    missing = [
        image
        for image in IMAGES
        if not any(
            g["built_before"] and g.get("severity") == "CRITICAL" and str(g.get("exit-code")) == "1"
            for g in gates.get(image, [])
        )
    ]
    assert not missing, f"no Trivy step fails on a fixable CRITICAL anywhere in {missing}: {gates}"


def test_ci_gates_each_sandbox_image_s_libraries_on_fixable_highs() -> None:
    gates = _scans("library")
    assert set(gates) == set(IMAGES), f"no Trivy step gates the libraries of {sorted(set(IMAGES) - set(gates))}"
    for image, found in gates.items():
        assert len(found) == 1, f"{image}: one library gate, not {len(found)}: {found}"
        gate = found[0]
        assert gate["built_before"], (
            f"{image}: the gate in job {gate['job']} scans an image no earlier step of that job builds and loads, "
            f"so it would scan nothing or a stale image: {gate}"
        )
        assert set(str(gate.get("severity", "")).split(",")) == {"HIGH", "CRITICAL"}, (
            f"{image}: the library gate must fail on HIGH as well as CRITICAL: {gate}"
        )
        assert str(gate.get("exit-code")) == "1", f"{image}: a scan that never fails the step gates nothing: {gate}"
        assert gate.get("ignore-unfixed") is True, f"{image}: a finding with no fix cannot be fixed by a bump: {gate}"
        assert gate.get("trivyignores") == ALLOWLIST.relative_to(ROOT).as_posix(), (
            f"{image}: the library gate must read the sandbox allowlist, {ALLOWLIST.relative_to(ROOT)}: {gate}"
        )


#: How far ahead an acceptance may expire: it is decided anew at least this often.
MAX_ACCEPTANCE_DAYS = 90
#: Where a sandbox image's libraries live, as Trivy reports a path: relative to the image's root.
_PATH_ROOTS = ("usr/", "opt/", "root/")
_CHARACTER_CLASS = re.compile(r"\[[^\]]*\]")


def _path_problem(path: object) -> str | None:
    """Why ``path`` does not narrow an acceptance to one package, or None."""
    if not isinstance(path, str) or not path.strip():
        return "an empty path"
    if "**" in path:
        return f"{path!r} matches at any depth (`**`)"
    if not path.startswith(_PATH_ROOTS):
        return f"{path!r} is not under {', '.join(_PATH_ROOTS)} as Trivy reports a path (relative, no leading /)"
    for part in path.split("/"):
        if part in ("", ".", ".."):
            return f"{path!r} has an empty, . or .. component"
        if not _CHARACTER_CLASS.sub("", part).replace("*", "").replace("?", ""):
            return f"{path!r} has a component that is only a wildcard ({part!r})"
    return None


def _sandbox_allowlist_problems(document: object, today: datetime.date) -> list[str]:
    """#305's rules (an id, a statement, an expiry), and for the sandbox images also: an expiry at most
    MAX_ACCEPTANCE_DAYS ahead of ``today``, and paths that each name one package."""
    problems = _allowlist_problems(document)
    entries = document.get("vulnerabilities") if isinstance(document, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        finding = entry.get("id") or entry
        expiry = entry.get("expired_at")
        if isinstance(expiry, datetime.date) and expiry > today + datetime.timedelta(days=MAX_ACCEPTANCE_DAYS):
            problems.append(
                f"{finding}: expires {expiry}, more than {MAX_ACCEPTANCE_DAYS} days after {today}, so it would go "
                "that long without being decided anew"
            )
        paths = entry.get("paths")
        if not (isinstance(paths, list) and paths):
            problems.append(f"{finding}: no paths narrow it to the package it was accepted in")
            continue
        problems.extend(f"{finding}: {why}" for why in map(_path_problem, paths) if why)
    return problems


def test_every_accepted_sandbox_finding_says_why_until_when_and_where() -> None:
    assert ALLOWLIST.is_file(), f"{ALLOWLIST.relative_to(ROOT)} is missing: CI's sandbox library gates read it"
    document = yaml.safe_load(ALLOWLIST.read_text(encoding="utf-8"))
    problems = _sandbox_allowlist_problems(document, datetime.date.today())
    assert problems == [], f"{ALLOWLIST.relative_to(ROOT)}: {problems}"


_TODAY = datetime.date(2026, 11, 3)
_GOOD = {
    "id": "CVE-0000-00001",
    "paths": ["usr/local/lib/python3.12/site-packages/example-*.dist-info/METADATA"],
    "statement": "no fixed release yet",
    "expired_at": _TODAY + datetime.timedelta(days=MAX_ACCEPTANCE_DAYS),
}


def test_a_reasoned_specific_acceptance_within_the_window_passes() -> None:
    assert _sandbox_allowlist_problems({"vulnerabilities": [_GOOD]}, _TODAY) == []


def test_the_sandbox_allowlist_check_refuses_an_acceptance_too_far_ahead() -> None:
    late = {**_GOOD, "expired_at": datetime.date(2099, 1, 1)}
    one_day = {**_GOOD, "expired_at": _TODAY + datetime.timedelta(days=MAX_ACCEPTANCE_DAYS + 1)}
    for entry in (late, one_day):
        problems = _sandbox_allowlist_problems({"vulnerabilities": [entry]}, _TODAY)
        assert len(problems) == 1 and f"more than {MAX_ACCEPTANCE_DAYS} days" in problems[0], (
            f"an acceptance expiring on {entry['expired_at']} must be refused: {problems}"
        )


@pytest.mark.parametrize(
    "path",
    [
        "**",
        "usr/**/METADATA",
        "*",
        "usr/lib/*/package.json",
        "usr/lib/node_modules/[a-z]*/package.json",
        "etc/example/package.json",
        "/usr/lib/node_modules/example/package.json",
        "usr/lib/../../etc/passwd",
        "",
    ],
)
def test_the_sandbox_allowlist_check_refuses_a_path_that_is_not_one_package(path: str) -> None:
    problems = _sandbox_allowlist_problems({"vulnerabilities": [{**_GOOD, "paths": [path]}]}, _TODAY)
    assert len(problems) == 1, f"the path {path!r} must be refused, and only once: {problems}"
