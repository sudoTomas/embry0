"""The orchestrator image ships no Node, and CI gates its libraries on HIGH (#305).

The image installed Node.js and `npm@latest` although nothing in it runs
them: every node, npm and npx command embry0 issues runs inside a sandbox,
through `docker exec`. npm's vendored undici carried three HIGH advisories
into the image, and CI's Trivy gate, CRITICAL only, built it green. Node is
gone, and a second Trivy step fails on a fixable HIGH or CRITICAL in the
image's libraries unless the allowlist accepts it, with a reason and an end.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR = ROOT / "infra" / "Dockerfile.orchestrator"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ALLOWLIST = ROOT / "infra" / "trivy" / "orchestrator.trivyignore.yaml"

_NODE = re.compile(r"nodesource|\bnodejs\b|\bnode\b|\bnpm\b|\bnpx\b|node_modules")


def _instructions(dockerfile: Path) -> list[str]:
    """Dockerfile instructions, one per entry: continuations joined, comments dropped."""
    lines = [ln for ln in dockerfile.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    return [" ".join(ins.split()) for ins in "\n".join(lines).replace("\\\n", " ").splitlines() if ins.strip()]


def _orchestrator_scans() -> list[dict[str, Any]]:
    """The `with:` of every Trivy step that scans the orchestrator image."""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["security"]["steps"]
    return [
        step["with"]
        for step in steps
        if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
        and step.get("with", {}).get("image-ref") == "embry0-orchestrator:ci"
    ]


def test_the_orchestrator_image_installs_no_node():
    found = [ins for ins in _instructions(ORCHESTRATOR) if _NODE.search(ins)]
    assert not found, (
        "Dockerfile.orchestrator installs Node or runs npm, and nothing in the orchestrator runs them: "
        f"every node, npm and npx command runs in a sandbox (#305): {found}"
    )


def test_ci_gates_the_orchestrator_libraries_on_fixable_highs():
    scans = _orchestrator_scans()
    libraries = [scan for scan in scans if scan.get("vuln-type") == "library"]
    assert len(libraries) == 1, f"no Trivy step gates the orchestrator image's libraries on their own: {scans}"
    gate = libraries[0]
    assert set(str(gate.get("severity", "")).split(",")) == {"HIGH", "CRITICAL"}, (
        f"the library gate must fail on HIGH as well as CRITICAL: {gate}"
    )
    assert str(gate.get("exit-code")) == "1", f"a scan that never fails the step gates nothing: {gate}"
    assert gate.get("ignore-unfixed") is True, f"a finding with no fix cannot be fixed by a bump: {gate}"
    assert gate.get("trivyignores") == ALLOWLIST.relative_to(ROOT).as_posix(), (
        f"the library gate must read the orchestrator's allowlist, {ALLOWLIST.relative_to(ROOT)}: {gate}"
    )


def test_the_orchestrator_os_packages_keep_their_critical_gate():
    whole = [scan for scan in _orchestrator_scans() if scan.get("vuln-type", "os,library") == "os,library"]
    assert any(scan.get("severity") == "CRITICAL" and str(scan.get("exit-code")) == "1" for scan in whole), (
        f"the whole orchestrator image, OS packages included, is no longer gated on fixable CRITICALs: {whole}"
    )


_SECTIONS = {"vulnerabilities", "misconfigurations", "secrets", "licenses"}


def _allowlist_problems(document: object) -> list[str]:
    """Why a .trivyignore.yaml document is not a list of reasoned, expiring acceptances."""
    if not isinstance(document, dict):
        return ["the allowlist is not a mapping: write `vulnerabilities: []` when nothing is accepted"]
    problems = [f"unknown section {key!r}: Trivy reads {sorted(_SECTIONS)}" for key in document if key not in _SECTIONS]
    entries = document.get("vulnerabilities")
    if not isinstance(entries, list):
        return [*problems, "`vulnerabilities` is not a list: write `vulnerabilities: []` when nothing is accepted"]
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
            problems.append(f"entry {index} names no finding id")
            continue
        finding = entry["id"]
        if not str(entry.get("statement") or "").strip():
            problems.append(f"{finding}: no statement says why it is accepted")
        if not isinstance(entry.get("expired_at"), datetime.date):
            problems.append(f"{finding}: no expired_at date, so the acceptance would never be reviewed")
    return problems


def test_every_accepted_finding_says_why_and_until_when():
    assert ALLOWLIST.is_file(), f"{ALLOWLIST.relative_to(ROOT)} is missing: CI's library gate reads it"
    problems = _allowlist_problems(yaml.safe_load(ALLOWLIST.read_text(encoding="utf-8")))
    assert problems == [], f"{ALLOWLIST.relative_to(ROOT)}: {problems}"


def test_the_allowlist_check_refuses_an_acceptance_without_a_reason_or_an_end():
    accepted = {"id": "CVE-0000-00002", "statement": "no fixed release yet", "expired_at": datetime.date(2026, 12, 31)}
    problems = _allowlist_problems({"vulnerabilities": [{"id": "CVE-0000-00001"}, accepted]})
    assert problems == [
        "CVE-0000-00001: no statement says why it is accepted",
        "CVE-0000-00001: no expired_at date, so the acceptance would never be reviewed",
    ], f"a bare id must be refused and a reasoned, expiring entry accepted: {problems}"
    problems = _allowlist_problems({"vulnerabilities": [{"statement": "no id"}]})
    assert problems == ["entry 0 names no finding id"], f"an entry with no id accepts nothing nameable: {problems}"
    problems = _allowlist_problems({"vulnerabilites": []})
    assert problems == [
        f"unknown section 'vulnerabilites': Trivy reads {sorted(_SECTIONS)}",
        "`vulnerabilities` is not a list: write `vulnerabilities: []` when nothing is accepted",
    ], f"a misspelt section must not pass as an empty allowlist: {problems}"
    problems = _allowlist_problems(None)
    assert problems == ["the allowlist is not a mapping: write `vulnerabilities: []` when nothing is accepted"], (
        f"a file of comments only must not pass: {problems}"
    )
