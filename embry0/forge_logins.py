"""The login owners are named by: one rule for every place that names a repository's or an area's owners.

Repository owners (``repo_preferences.owners``), area owners (the areas
document, spec 2026-11-10) and the people a chat assistant may ask are all
matched against GitHub logins (``users.github_login``), so all of them are
checked with this one pattern. Leaf module: imports nothing of embry0.
"""

from __future__ import annotations

import re
from typing import Final

#: A GitHub login: up to 39 letters, digits and single hyphens, not starting or ending with one.
GITHUB_LOGIN_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
