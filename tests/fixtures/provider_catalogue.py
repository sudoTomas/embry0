"""The provider catalogue a test runs with (#173): the shipped one with some providers turned on."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents import catalogue


def enable_providers(monkeypatch: pytest.MonkeyPatch, *names: str, **overlay: Any) -> catalogue.Catalogue:
    """Install, for this test, the shipped catalogue with the OpenAI-compatible providers ``names`` turned on (and
    any other provider fields given as ``overlay``, ``{name: {field: value}}``); the test's end puts back what was
    installed. The catalogue's ``enabled``, not ``OPENAI_COMPATIBLE_PROVIDERS``, turns a provider on."""
    providers: dict[str, dict[str, Any]] = {name: {"enabled": True} for name in names}
    for name, fields in overlay.items():
        providers.setdefault(name, {}).update(fields)
    built = catalogue.Catalogue.build(catalogue.merge(catalogue.seed().document, {"providers": providers}))
    monkeypatch.setattr(catalogue, "_installed", built)
    return built


async def store_providers(db: Any, *names: str, **overlay: Any) -> catalogue.Catalogue:
    """:func:`enable_providers` for a test with a database: the same overlay stored in ``provider_catalogue`` too, so
    that a store reading it (a write naming a model, ``GET /config/providers/catalog``) installs the same. The
    database's reset empties the row again; the autouse fixture puts the shipped catalogue back."""
    import json

    import yaml

    providers: dict[str, dict[str, Any]] = {name: {"enabled": True} for name in names}
    for name, fields in overlay.items():
        providers.setdefault(name, {}).update(fields)
    document = {"providers": providers} if providers else {}
    built = catalogue.Catalogue.build(catalogue.merge(catalogue.seed().document, document or None))
    await db.execute("INSERT INTO provider_catalogue (scope) VALUES ('deployment') ON CONFLICT (scope) DO NOTHING")
    await db.execute(
        "UPDATE provider_catalogue SET yaml_text = $1, data = $2::jsonb, generation = generation + 1 "
        "WHERE scope = 'deployment'",
        yaml.safe_dump(document, sort_keys=False) if document else "",
        json.loads(json.dumps(document)),
    )
    catalogue.install(built)
    return built
