#!/usr/bin/env python3
"""qa-ready-check — poll a list of HTTP ready-checks until they all pass or
a timeout expires. Used by the QA agent's `boot` phase.

Usage:
    qa-ready-check --timeout SEC --check URL[,STATUS[,REGEX]] ...

Each --check is "URL[,STATUS[,REGEX]]". Defaults: STATUS=200, no body regex.

Exits 0 when every check passes within --timeout. Exits 1 (and emits a
JSON status to stdout) on timeout. Re-emits a JSON line per attempt to
stderr so the orchestrator's qa.boot_progress event consumer can ingest
them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass
class Check:
    url: str
    expect_status: int = 200
    expect_body_regex: str | None = None


def parse_check(spec: str) -> Check:
    parts = spec.split(",", 2)
    url = parts[0]
    status = int(parts[1]) if len(parts) > 1 and parts[1] else 200
    regex = parts[2] if len(parts) > 2 and parts[2] else None
    return Check(url=url, expect_status=status, expect_body_regex=regex)


def try_check(c: Check) -> tuple[bool, str]:
    """Returns (passed, reason)."""
    try:
        req = urlrequest.Request(c.url, headers={"User-Agent": "qa-ready-check/1"})
        with urlrequest.urlopen(req, timeout=5) as resp:
            status = resp.status
            body = resp.read(8192).decode("utf-8", errors="replace") if c.expect_body_regex else ""
        if status != c.expect_status:
            return False, f"status={status} (want {c.expect_status})"
        if c.expect_body_regex and not re.search(c.expect_body_regex, body):
            return False, "body did not match expect_body_regex"
        return True, "ok"
    except urlerror.HTTPError as e:
        return False, f"http_error status={e.code}"
    except urlerror.URLError as e:
        return False, f"url_error reason={e.reason}"
    except Exception as e:  # noqa: BLE001 — last resort
        return False, f"exception={type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=int, required=True, help="Total budget in seconds")
    p.add_argument("--check", action="append", required=True, help="URL[,STATUS[,REGEX]]")
    p.add_argument("--interval", type=float, default=2.0, help="Seconds between rounds (default 2.0)")
    args = p.parse_args(argv)

    checks = [parse_check(s) for s in args.check]
    deadline = time.monotonic() + args.timeout
    attempt = 0
    last_failures: list[dict] = []
    while True:
        attempt += 1
        last_failures = []
        for c in checks:
            ok, reason = try_check(c)
            print(
                json.dumps(
                    {
                        "type": "qa.boot_progress",
                        "attempt": attempt,
                        "url": c.url,
                        "status": "passing" if ok else "failing",
                        "reason": reason,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            if not ok:
                last_failures.append({"url": c.url, "reason": reason})
        if not last_failures:
            print(json.dumps({"status": "ready", "attempts": attempt}), flush=True)
            return 0
        if time.monotonic() >= deadline:
            print(json.dumps({"status": "timeout", "attempts": attempt, "failures": last_failures}), flush=True)
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
