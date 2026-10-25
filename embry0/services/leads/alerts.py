"""Dependency and code-scanning alerts (leads, spec 2026-10-25).

Reads the repository's open Dependabot alerts and open code-scanning alerts.
Each alert is one lead, keyed by its source and number. A source the
organization's credential may not read (403, or 404 where the feature is off)
is skipped quietly: the repository simply offers none from it.
"""

from __future__ import annotations

from typing import Any

from embry0.services.leads import FinderResult, Lead, skipped

KIND = "alert"
#: Alerts read per source per pass.
PAGE = 100
_UNREADABLE = frozenset({401, 403, 404, 410})


def _severity(value: Any) -> str:
    text = str(value or "").lower()
    return {
        "critical": "critical",
        "high": "high",
        "error": "high",
        "moderate": "medium",
        "medium": "medium",
        "warning": "medium",
        "low": "low",
        "note": "low",
    }.get(text, "medium")


def dependabot_lead(repo: str, alert: dict[str, Any]) -> Lead | None:
    number = alert.get("number")
    if number is None:
        return None
    dependency = alert.get("dependency") or {}
    package = (dependency.get("package") or {}).get("name") or "a dependency"
    ecosystem = (dependency.get("package") or {}).get("ecosystem") or ""
    manifest = str(dependency.get("manifest_path") or "")
    advisory = alert.get("security_advisory") or {}
    vulnerability = alert.get("security_vulnerability") or {}
    fixed = ((vulnerability.get("first_patched_version") or {}).get("identifier")) or ""
    severity = _severity(vulnerability.get("severity") or advisory.get("severity"))
    ref = advisory.get("ghsa_id") or advisory.get("cve_id") or f"alert {number}"
    return Lead(
        kind=KIND,
        repo=repo,
        source_key=f"dependabot:{number}",
        title=f"{package}: {advisory.get('summary') or 'a known vulnerability'}"[:200],
        summary=(
            f"Dependabot reports a {severity} vulnerability in {package}"
            + (f" ({manifest})" if manifest else "")
            + (f"; {fixed} fixes it." if fixed else "; no fixed version yet.")
        ),
        details="\n".join(
            part
            for part in (
                f"{ref}, {ecosystem} package {package}" if ecosystem else f"{ref}, package {package}",
                f"Vulnerable: {vulnerability.get('vulnerable_version_range')}"
                if vulnerability.get("vulnerable_version_range")
                else "",
                f"First fixed version: {fixed}" if fixed else "",
                str(advisory.get("description") or "").strip()[:1500],
            )
            if part
        ),
        action=f"Start a job that upgrades {package} to a fixed version"
        if fixed
        else f"Start a job that removes or replaces {package}",
        url=alert.get("html_url"),
        severity=severity,
        effort="small" if fixed else "medium",
        paths=(manifest,) if manifest else (),
        data={"source": "dependabot", "number": number, "package": package, "fixed": fixed, "manifest": manifest},
    )


def code_scanning_lead(repo: str, alert: dict[str, Any]) -> Lead | None:
    number = alert.get("number")
    if number is None:
        return None
    rule = alert.get("rule") or {}
    instance = alert.get("most_recent_instance") or {}
    location = instance.get("location") or {}
    path = str(location.get("path") or "")
    line = location.get("start_line")
    where = f"{path}:{line}" if path and line else path
    description = str(rule.get("description") or rule.get("name") or rule.get("id") or "a code-scanning rule")
    tool = (alert.get("tool") or {}).get("name") or "code scanning"
    return Lead(
        kind=KIND,
        repo=repo,
        source_key=f"code-scanning:{number}",
        title=f"{description}" + (f" in {where}" if where else ""),
        summary=f"{tool} reports {description}" + (f" at {where}." if where else "."),
        details="\n".join(
            part
            for part in (
                f"Rule {rule.get('id')}" if rule.get("id") else "",
                str((instance.get("message") or {}).get("text") or "").strip()[:1500],
            )
            if part
        ),
        action=f"Start a job that fixes what {tool} reports" + (f" in {path}" if path else ""),
        url=alert.get("html_url"),
        severity=_severity(rule.get("security_severity_level") or rule.get("severity")),
        effort="medium",
        paths=(path,) if path else (),
        data={"source": "code-scanning", "number": number, "rule": rule.get("id"), "path": path, "line": line},
    )


async def find_alerts(client: Any, repo: str) -> FinderResult:
    """Every open Dependabot and code-scanning alert the credential may read."""
    leads: list[Lead] = []
    complete = True
    readable = 0
    for path, build in (
        (f"/repos/{repo}/dependabot/alerts", dependabot_lead),
        (f"/repos/{repo}/code-scanning/alerts", code_scanning_lead),
    ):
        resp = await client.get(path, repo=repo, params={"state": "open", "per_page": PAGE})
        if resp.status_code in _UNREADABLE:
            continue
        if resp.status_code != 200 or not isinstance(resp.json(), list):
            complete = False
            continue
        readable += 1
        alerts = [a for a in resp.json() if isinstance(a, dict)]
        complete = complete and len(alerts) < PAGE
        leads += [lead for alert in alerts if (lead := build(repo, alert)) is not None]
    if not readable and complete:
        return skipped("alerts cannot be read with the organization's credential")
    return FinderResult(leads, complete=complete)
