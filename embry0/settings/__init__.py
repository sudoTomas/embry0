"""Operator policy in a settings document stored in the database (spec 2026-10-28).

One document per deployment, YAML text with comments, one section per
feature. A feature declares its section in ``embry0/settings/sections/``
(:mod:`embry0.settings.registry`), reads it through
:class:`embry0.settings.store.SettingsStore`, and never through an
environment variable.

This package's ``__init__`` imports nothing, so a section module can import
the registry without pulling in the store, the database or the config.
"""
