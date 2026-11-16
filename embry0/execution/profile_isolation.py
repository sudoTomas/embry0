"""The sandbox-profile values that keep a sandbox isolated, and nothing wider (#553).

A sandbox profile is operator tier. Four of its fields reach ``docker run`` or
``docker network connect`` and decide how isolated the sandbox is:

- ``cap_drop`` / ``cap_add``: a capability added back, or a drop list without
  ``ALL``, hands the container capabilities;
- ``security_opt``: any list replaces the default ``no-new-privileges``, and can
  switch seccomp and AppArmor off;
- ``base_image``: a registry-qualified name is pulled from that registry by tag,
  past embry0's own registry and its digest pins;
- ``extra_networks``: the private-range egress drop is installed only for
  ``sandbox-internet``, so any other network is egress with no drop, or another
  job's network.

Weakening any of these is the same class of power as the shared Docker daemon,
which takes an admin's per-repo grant (#522). A profile must not reach it, so
the only values allowed are the safe defaults every builtin profile uses.

Checked twice: by the API schema when a profile is written, and by
``SandboxManager.create`` for every sandbox. A row stored before this check, or
written around the API, fails closed at create.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from embry0.execution.image_registry import is_registry_qualified

# The one extra network a profile may join: NAT egress with the private-range
# drop that SandboxManager.create installs for it.
ALLOWED_EXTRA_NETWORKS = frozenset({"sandbox-internet"})

# Docker's spellings of no-new-privileges switched on.
_NO_NEW_PRIVILEGES = frozenset({"no-new-privileges", "no-new-privileges=true", "no-new-privileges:true"})

# An image in embry0's own registry, named without a registry host:
# path components of lowercase alnum joined by `.`, `_` or `-`, an optional tag
# and an optional sha256 digest. Starts alnum, so it can never read as a flag.
_LOCAL_IMAGE_RE = re.compile(
    r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    r"(?:@sha256:[0-9a-f]{64})?"
)


def _as_list(field: str, value: Any) -> tuple[list[Any] | None, str | None]:
    """``value`` as a list, or the reason it is not one. None means unset."""
    if value is None:
        return [], None
    if not isinstance(value, list):
        return None, f"{field}: must be a list, got {type(value).__name__}"
    return value, None


def cap_drop_problem(value: Any) -> str | None:
    """Every sandbox drops all capabilities. Empty means the default, ``["ALL"]``."""
    caps, problem = _as_list("cap_drop", value)
    if caps is None:
        return problem
    if caps and not any(isinstance(cap, str) and cap.strip().upper() == "ALL" for cap in caps):
        return f"cap_drop: must include ALL, got {caps!r}; a sandbox profile cannot keep Linux capabilities"
    return None


def cap_add_problem(value: Any) -> str | None:
    """No capability is added back. No builtin profile adds one."""
    caps, problem = _as_list("cap_add", value)
    if caps is None:
        return problem
    if caps:
        return f"cap_add: must be empty, got {caps!r}; a sandbox profile cannot add Linux capabilities back"
    return None


def security_opt_problem(value: Any) -> str | None:
    """``no-new-privileges`` and nothing else. Empty means the default, ``["no-new-privileges"]``."""
    opts, problem = _as_list("security_opt", value)
    if opts is None:
        return problem
    others = [opt for opt in opts if not (isinstance(opt, str) and opt in _NO_NEW_PRIVILEGES)]
    if others:
        return (
            f"security_opt: only 'no-new-privileges' is allowed, got {others!r}; "
            "a sandbox profile cannot change seccomp, AppArmor or privilege escalation"
        )
    return None


def base_image_problem(value: Any, registry: str = "") -> str | None:
    """An image in embry0's own registry.

    The API stores names unqualified (``embry0-sandbox:latest``), and
    ``qualify_image`` prefixes ``registry`` at launch. At create, a name already
    qualified with this deployment's ``registry`` is the same image and passes;
    any other registry host does not.
    """
    if not isinstance(value, str) or not value:
        return f"base_image: must be an image name, got {value!r}"
    name = value
    prefix = f"{registry.rstrip('/')}/" if registry else ""
    if prefix and name.startswith(prefix):
        name = name[len(prefix) :]
    if is_registry_qualified(name):
        return (
            f"base_image: {value!r} names a registry host; a sandbox profile can only use an image "
            "from embry0's own registry, named without one (e.g. 'embry0-sandbox:latest')"
        )
    if not _LOCAL_IMAGE_RE.fullmatch(name):
        return f"base_image: {value!r} is not a valid image name"
    return None


def extra_networks_problem(value: Any) -> str | None:
    """``sandbox-internet`` or nothing."""
    nets, problem = _as_list("extra_networks", value)
    if nets is None:
        return problem
    others = [net for net in nets if not (isinstance(net, str) and net in ALLOWED_EXTRA_NETWORKS)]
    if others:
        return (
            f"extra_networks: only 'sandbox-internet' is allowed, got {others!r}; "
            "it is the one network a sandbox joins with the private-range egress drop"
        )
    return None


ISOLATION_FIELDS = ("cap_drop", "cap_add", "security_opt", "base_image", "extra_networks")


def field_problem(field: str, value: Any, registry: str = "") -> str | None:
    """Why ``value`` for profile ``field`` would weaken isolation, or None.

    KeyError for a field outside ISOLATION_FIELDS: a typo must not read as a pass.
    """
    checks: dict[str, Callable[[Any], str | None]] = {
        "cap_drop": cap_drop_problem,
        "cap_add": cap_add_problem,
        "security_opt": security_opt_problem,
        "base_image": lambda image: base_image_problem(image, registry),
        "extra_networks": extra_networks_problem,
    }
    return checks[field](value)


def profile_isolation_problems(profile: Mapping[str, Any], registry: str = "") -> list[str]:
    """Every reason ``profile`` would weaken a sandbox's isolation. Empty when it would not.

    A missing key means that field's default, which is always safe.
    """
    problems = (field_problem(field, profile[field], registry) for field in ISOLATION_FIELDS if field in profile)
    return [problem for problem in problems if problem is not None]
