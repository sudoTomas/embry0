"""Send a started proxy's upstream calls to a stand-in, without changing the application's state.

aiohttp deprecates changing a started application's state, so a test does not
replace the proxy's ``HTTP_CLIENT`` once the proxy runs. It keeps the proxy's
own httpx client and points that client's ``get``, ``request`` and ``stream``
at the stand-in's; the proxy's cleanup still closes the real client.
"""

from __future__ import annotations

from aiohttp import web

from embry0.execution.proxy.app_keys import HTTP_CLIENT

_METHODS = ("get", "request", "stream")


def route_upstream(app: web.Application, fake: object) -> None:
    """Route the started proxy's upstream calls to ``fake``, which has at least one of ``get``, ``request``, ``stream``."""
    client = app[HTTP_CLIENT]
    routed = [name for name in _METHODS if hasattr(fake, name)]
    assert routed, f"{type(fake).__name__} has none of {_METHODS}: the proxy would still call the real upstream"
    for name in routed:
        setattr(client, name, getattr(fake, name))
