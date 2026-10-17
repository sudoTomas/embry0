# ADR 0011: Needs are derived from facts, and only a person's answer is kept

Status: accepted (2026-10-16)

The needs list is the reading of an owner's newest finished discovery run ([ADR 0010](0010-organizations.md)): per kind, from a closed vocabulary (`embry0/services/org_needs.py`), the repositories it covers and the one action that answers it. A run syncs its needs when it finishes; a read writes nothing except a one-time catch-up, under a Postgres advisory lock per owner. `org_needs` (migration 99) keeps one row per owner, kind and repository set, so a dismissal survives rediscovery and a need no longer derived is `done`. Each open need is a healing case with origin `org`, awaiting a decision: it holds no proposal-cap slot, cannot be accepted, and closing it dismisses the need.

A hook points here only when its URL starts with `WEBHOOK_PUBLIC_URL`; unset, or on a loopback host no forge can reach, hooks are unknown, not absent. Reading an unset origin as "no hook" would report every repository as unhooked while hooks to a webhooks hostname configured outside the repository exist; `tests/unit/services/test_org_discovery.py` pins that they read as unknown instead.
