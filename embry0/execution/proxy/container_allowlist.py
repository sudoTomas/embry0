"""The context proxy's container allowlist: exact names and glob patterns.

``CONTEXT_PROXY_CONTAINER_ALLOWLIST`` names the containers an agent may read
logs from. A deployment that names its containers per release (blue/green
slots such as ``shop-api-blue`` and ``shop-api-green``, a copy per release)
cannot list those names ahead of time, so an entry may be a glob pattern:
``shop-api*``.

- An entry without any of ``*?[`` is an exact name, matched case-insensitively,
  and answers with its own spelling. This is the behaviour every allowlist had
  before patterns existed.
- A pattern entry (``fnmatch``, case-insensitive) admits a NAME that matches it,
  as long as that name is a plain container name: letters, digits, ``_`` and
  ``-`` only. The name goes on to the log API's container selector, which embeds
  it in a LogQL regex unescaped, so nothing a regex would read as syntax (``.``,
  ``|``, ``*``, ``(``) may get in through a pattern. A pattern entry is never a
  queryable name itself.

Used on both sides of the proxy boundary: the context proxy checks queries and
enrollment scopes against it, and the orchestrator resolves which allowlisted
containers a ticket's evidence names. Keep it free of imports beyond the
standard library: the proxy image copies modules one by one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from fnmatch import fnmatchcase

_GLOB_CHARS = frozenset("*?[")

#: A name a pattern may admit. 200 is the log API's own cap on ``container``.
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")

# A run of text that could be a container name, as a whole word: not preceded
# or followed by a word character or a hyphen.
_NAME_TOKEN = re.compile(r"(?<![\w-])[A-Za-z0-9][A-Za-z0-9_-]*(?![\w-])")


def is_pattern(entry: str) -> bool:
    """Whether an allowlist entry is a glob pattern rather than an exact name."""
    return any(c in _GLOB_CHARS for c in entry)


class ContainerAllowlist:
    """Allowlist entries (exact names and glob patterns) and the names they admit."""

    def __init__(self, entries: Iterable[str]) -> None:
        self.entries: list[str] = [e.strip() for e in entries if e and e.strip()]
        self._exact: dict[str, str] = {e.lower(): e for e in self.entries if not is_pattern(e)}
        self._patterns: list[str] = [e.lower() for e in self.entries if is_pattern(e)]

    def __bool__(self) -> bool:
        return bool(self.entries)

    @property
    def has_patterns(self) -> bool:
        return bool(self._patterns)

    def resolve(self, name: str) -> str | None:
        """The name to query for ``name``, or None when the allowlist does not admit it.

        An exact entry answers with the entry's own spelling; a name admitted by a
        pattern answers as given (stripped).
        """
        clean = (name or "").strip()
        if not clean:
            return None
        exact = self._exact.get(clean.lower())
        if exact is not None:
            return exact
        if self._patterns and PLAIN_NAME.fullmatch(clean):
            lowered = clean.lower()
            if any(fnmatchcase(lowered, pattern) for pattern in self._patterns):
                return clean
        return None

    def names_in(self, text: str) -> list[str]:
        """Admitted container names that appear in ``text`` as whole names, sorted."""
        if not text:
            return []
        found: set[str] = set()
        for lowered, entry in self._exact.items():
            if re.search(rf"(?<![\w-]){re.escape(lowered)}(?![\w-])", text, re.IGNORECASE):
                found.add(entry)
        if self._patterns:
            for token in _NAME_TOKEN.findall(text):
                resolved = self.resolve(token)
                if resolved is not None:
                    found.add(resolved)
        return sorted(found)
