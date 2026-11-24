"""The login owners are named by: one rule for every place that names a repository's or an area's owners.

Repository owners (``repo_preferences.owners``), area owners (the areas
document, spec 2026-11-10), the people a chat assistant may ask, a user's
GitHub login (``users.github_login``) and a person's ``github`` identity
(``person_identities``, #179) are all matched against each other, so all of
them are checked with this one pattern, GitHub's own: a login GitHub would not
let anyone sign up with can be nobody's. Leaf module: imports nothing of
embry0.
"""

from __future__ import annotations

import re
from typing import Final

#: A GitHub login: up to 39 letters, digits and single hyphens, not starting or ending with one.
GITHUB_LOGIN_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
#: A GitLab username: letters, digits, underscores, dots and hyphens, starting with a letter, digit or underscore.
GITLAB_LOGIN_RE: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
