"""Agent identity vocabulary: the avatar roster and the color palette.

One source for the backend validators and, through docs/contracts/identity.json,
for the frontend (frontend/src/lib/identity.ts). The frontend keys its sprites
and tints by these ids, so an id is never renamed once released; a new one is
appended.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Final

AVATARS: Final[tuple[str, ...]] = (
    "seer",
    "smith",
    "warden",
    "alchemist",
    "herald",
    "arbiter",
    "scholar",
    "oracle",
    "engineer",
    "rogue",
    "bard",
    "golem",
)
PALETTE: Final[tuple[str, ...]] = ("gold", "cyan", "violet", "rose", "emerald", "amber", "sky", "slate")
DEFAULT_AVATAR: Final = "golem"
DEFAULT_COLOR: Final = "slate"
DISPLAY_NAME_MAX: Final = 60
TAGLINE_MAX: Final = 140

#: Identity of every builtin agent row; seeds and migration 101 write these.
BUILTIN_IDENTITY: Final[dict[str, dict[str, str]]] = {
    "triage": {
        "display_name": "Triage",
        "avatar": "seer",
        "color": "violet",
        "tagline": "Reads the issue, sets the course.",
    },
    "developer": {"display_name": "Developer", "avatar": "smith", "color": "amber", "tagline": "Forges the change."},
    "review": {"display_name": "Reviewer", "avatar": "warden", "color": "rose", "tagline": "Guards the gate."},
    "qa": {"display_name": "QA", "avatar": "alchemist", "color": "emerald", "tagline": "Proves it in the vessel."},
    "onboarding": {
        "display_name": "Onboarding",
        "avatar": "herald",
        "color": "sky",
        "tagline": "Announces a new repository.",
    },
    "pr-reviewer": {
        "display_name": "PR Reviewer",
        "avatar": "arbiter",
        "color": "cyan",
        "tagline": "Weighs a pull request.",
    },
    "research": {
        "display_name": "Research",
        "avatar": "scholar",
        "color": "gold",
        "tagline": "Reads everything first.",
    },
    "analysis": {"display_name": "Analysis", "avatar": "oracle", "color": "violet", "tagline": "Finds the pattern."},
    "ops": {"display_name": "Ops", "avatar": "engineer", "color": "amber", "tagline": "Keeps it running."},
}


def validate_avatar(value: str) -> str:
    if value not in AVATARS:
        raise ValueError(f"avatar must be one of {', '.join(AVATARS)}; got {value!r}")
    return value


def validate_color(value: str) -> str:
    if value not in PALETTE:
        raise ValueError(f"color must be one of {', '.join(PALETTE)}; got {value!r}")
    return value


def contract() -> dict[str, Any]:
    """The identity vocabulary as docs/contracts/identity.json carries it."""
    return {
        "avatars": list(AVATARS),
        "palette": list(PALETTE),
        "default_avatar": DEFAULT_AVATAR,
        "default_color": DEFAULT_COLOR,
        "display_name_max": DISPLAY_NAME_MAX,
        "tagline_max": TAGLINE_MAX,
        "builtin": BUILTIN_IDENTITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="embry0 agent identity contract")
    parser.add_argument("--write-contract", metavar="PATH", help="write docs/contracts/identity.json")
    args = parser.parse_args(argv)
    if args.write_contract:
        with open(args.write_contract, "w", encoding="utf-8") as fh:
            json.dump(contract(), fh, indent=2, sort_keys=True)
            fh.write("\n")
    else:
        sys.stdout.write(json.dumps(contract(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
