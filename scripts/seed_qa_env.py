#!/usr/bin/env python3
"""Seed a repo's QA env vars into embry0 from a dotenv file.

Run this MANUALLY after a Postgres reset. embry0 stores per-repo QA env
vars in the `repo_environment` table (Fernet-encrypted) — a DB reset wipes
them. The durable source of truth is the dotenv seed file; this script
replays it into the env API.

The PUT endpoint is replace-all (DELETE-then-INSERT for the repo), so this
is idempotent: every run leaves repo_environment exactly matching the seed
file, no duplicates, no stale keys.

Usage:
    QA_TARGET_REPO=owner/name scripts/seed_qa_env.py [SEED_FILE]

    QA_TARGET_REPO  target repo as owner/name (required)
    SEED_FILE       dotenv file (default: <repo>/.env.qa-seed)

Reads API_KEY from <repo>/.env for auth. Prints key names + counts only —
never values.

Exit codes: 0 ok · 1 usage/parse error · 2 API error.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED = REPO_ROOT / ".env.qa-seed"
EMBRY0_ENV = REPO_ROOT / ".env"
API_BASE = "http://localhost:8200"
TARGET_REPO = os.environ.get("QA_TARGET_REPO", "")  # owner/name

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a dotenv file → {key: value}. Tolerates `export `, quotes,
    inline comments after unquoted values, blank lines, and `#` comments."""
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip surrounding quotes; only strip an inline comment when the
        # value was NOT quoted (a '#' inside a quoted secret is legitimate).
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        else:
            hash_idx = val.find(" #")
            if hash_idx != -1:
                val = val[:hash_idx].rstrip()
        if not _KEY_RE.match(key):
            print(f"  ! skipping invalid key name: {key!r}", file=sys.stderr)
            continue
        out[key] = val
    return out


def _api_key() -> str:
    for line in EMBRY0_ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("Could not find API_KEY in embry0 .env")


def main() -> int:
    if not TARGET_REPO or "/" not in TARGET_REPO:
        print("QA_TARGET_REPO must be set to owner/name", file=sys.stderr)
        return 1
    seed_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SEED
    if not seed_path.is_file():
        print(f"Seed file not found: {seed_path}", file=sys.stderr)
        return 1

    pairs = _read_dotenv(seed_path)
    if not pairs:
        print(f"No KEY=value pairs parsed from {seed_path}", file=sys.stderr)
        return 1

    # Every QA secret is scope='app' (scope='qa' would require a QA_ prefix
    # per the env schema). var_type='secret' so values are masked in the
    # API's GET responses; functionally identical to 'config' for sandbox
    # injection.
    variables = [
        {"key": k, "value": v, "var_type": "secret", "required": False, "scope": "app"} for k, v in pairs.items()
    ]

    owner, name = TARGET_REPO.split("/", 1)
    url = f"{API_BASE}/api/v1/repos/{owner}/{name}/environment"
    body = json.dumps({"variables": variables}).encode()
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {_api_key()}")
    req.add_header("X-Requested-With", "XMLHttpRequest")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"Request failed: {e}", file=sys.stderr)
        return 2

    returned = sorted(v["key"] for v in payload.get("variables", []))
    print(f"✓ Seeded {len(returned)} env vars into {TARGET_REPO} from {seed_path.name}")
    print("  keys:", ", ".join(returned))
    sent = set(pairs)
    got = set(returned)
    if sent - got:
        print(f"  ! sent but not echoed back: {sorted(sent - got)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
