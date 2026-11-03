"""The keys two or more proxies keep their state under in their aiohttp application.

aiohttp warns (``NotAppKeyWarning``) for a plain string key. A ``web.AppKey``
also names its value's type, so ``request.app[HTTP_CLIENT]`` is an
``httpx.AsyncClient`` to the type checker without an annotation at each read.
A key one module alone uses is defined in that module, beside its value's type
(``ENROLLMENT`` in ``enrollment``, ``GIT_GRANT_BY_ID`` in ``git_scope``).
"""

from __future__ import annotations

import aiohttp
import httpx
from aiohttp import web

#: The upstream client of a proxy that forwards with httpx.
HTTP_CLIENT = web.AppKey("http_client", httpx.AsyncClient)
#: The upstream session of a proxy that forwards with aiohttp (it streams bodies through).
HTTP_SESSION = web.AppKey("http_session", aiohttp.ClientSession)
#: The upstream a forwarding proxy sends every request to.
UPSTREAM_URL = web.AppKey("upstream_url", str)
#: The deployment's GitHub token, the fallback when an enrollment carries none.
GITHUB_TOKEN = web.AppKey("github_token", str)
#: A private CA's PEM the upstream TLS context trusts, or "".
CA_PEM = web.AppKey("ca_pem", str)
