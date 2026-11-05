"""Production gives the sandbox manager the deployment's instance id.

Without it every JVM sandbox gets a per-job Maven cache: nothing fails, the
caches are just never shared within a repository, which looks exactly like
a cold cache (the #369 shape). Read from the AST of ``lifespan``, which needs
Docker and a database; see test_retention_wiring.py for the same approach.
"""

from __future__ import annotations

import ast
import pathlib

import embry0.api.app as app_module


def test_the_sandbox_manager_is_given_the_resolved_instance_id() -> None:
    tree = ast.parse(pathlib.Path(app_module.__file__).read_text())
    resolved = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]) == "volume_instance_id"
        and "fetch_instance_id" in ast.unparse(node.value)
    ]
    given = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]) == "sandbox_mgr.instance_id"
        and ast.unparse(node.value) == "volume_instance_id"
    ]
    assert resolved, "embry0/api/app.py no longer resolves volume_instance_id with fetch_instance_id"
    assert given, "app.py never sets sandbox_mgr.instance_id: every JVM sandbox would get a per-job Maven cache"
    assert min(given) > min(resolved), "sandbox_mgr.instance_id is set before the instance id is resolved"
