"""The ``repo`` path convertor: how a ``/repos/...`` route reads the repository it names (#243).

A repository is at least ``owner/name``, and a GitLab project in a subgroup
has more segments (``acme/products/shop``, #162). The ``/repos`` routes read
the repository with Starlette's ``path`` convertor, which matches a single
segment too. So ``GET /repos/acme/history`` was the history of a repository
called ``acme``, which cannot exist, and the repository ``acme/history``
could not be read: the sub-resource routes are registered before the detail
route.

``{full_repo:repo}`` matches two or more segments and nothing shorter, so a
path of two segments is always a repository's own address. A path of three
or more is still read by registration order (``acme/widgets/history`` is
the history of ``acme/widgets``); ``tests/unit/api/test_repos_route_order.py``
pins that order.

Each segment follows the one repository rule every request schema checks
(``SEGMENT`` in ``embry0/api/schemas/repo_path.py``): it starts with a letter
or digit and holds only letters, digits, ``_``, ``.`` and ``-``. So a path
segment carrying a newline, a space or another control character, or a
``.`` or ``..`` segment, names no repository and the request matches no
``/repos`` route. A route can no longer write a repository that ``POST
/repos`` would refuse, as ``PUT /repos/acme/widgets%0A/qa-config`` could
with a looser rule.

``embry0.api`` imports this module, so the convertor is registered before
any router module declares a route: a route compiles its path when it is
declared, and an unknown convertor fails there.
"""

from __future__ import annotations

from starlette.convertors import Convertor, register_url_convertor

from embry0.api.schemas.repo_path import SEGMENT

#: The convertor's name in a route path: ``/repos/{full_repo:repo}/history``.
REPO_CONVERTOR = "repo"


class RepoConvertor(Convertor[str]):
    """Two or more segments of the repository rule: ``owner/name`` or ``group/subgroup/name``."""

    regex = rf"{SEGMENT}(?:/{SEGMENT})+"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return str(value)


register_url_convertor(REPO_CONVERTOR, RepoConvertor())
