# ADR 0026: Operator policy lives in a settings document, one section per feature

Status: accepted (2026-10-28)

What an operator decides about how a feature behaves (thresholds, schedules, caps, which repositories a loop reads) is a key in one settings document per deployment, stored in the database, not an environment variable and not a file in a target repository. Each feature declares its section as a model in `embry0/settings/sections/`, the only home of its defaults and bounds; a write is validated as a whole and refused naming every bad key; every change is a revision, and the newest can be undone; loops read their section every tick, so a change needs no restart. A policy variable a section replaces is imported once, then ignored, then removed. The environment keeps secrets, the deployment's own addresses, container paths, process limits and pre-database switches.

Evidence: migrations 128 and 129; `embry0/settings/`; `embry0/api/v1/settings.py`; `docs/superpowers/specs/2026-10-28-settings-documents-design.md`; issues #159 and #160.
