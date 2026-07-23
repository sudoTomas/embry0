"""Pluggable workspace initialization strategies (RAV-600).

One initializer per job-context type (``git`` / ``http`` / ``local`` /
``none``) populates ``/workspace`` inside a freshly created sandbox.
The sandbox lifecycle itself (create/destroy) stays with the caller —
only workspace population varies per strategy.
"""

from embry0.workspace_init.base import (
    HttpFetchError,
    InitContext,
    LocalContextDeniedError,
    WorkspaceInitError,
    WorkspaceInitializer,
)
from embry0.workspace_init.registry import available_initializer_names, load_initializer

__all__ = [
    "HttpFetchError",
    "InitContext",
    "LocalContextDeniedError",
    "WorkspaceInitError",
    "WorkspaceInitializer",
    "available_initializer_names",
    "load_initializer",
]
