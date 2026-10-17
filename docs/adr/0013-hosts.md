# ADR 0013: A host is a first-class record, apart from the repository

Status: accepted (2026-10-16)

The facts a maintainer needs after a merge are machine facts: where the logs are (a journald unit, a container, a Loki selector) and whether the deploy landed. A repository row cannot hold them, because one repository runs on several machines and one machine runs several repositories. So a host is a row of its own (`hosts`): name, address, transport (`tailnet`, `ssh`, `http`), roles, log source and status, linked to repositories by `repo_hosts` with a role. Code that reads a machine's logs or deploy markers takes them from its host, not from repository settings.

A hand-kept host list drifts, so a tailnet, where machines already announce themselves, is a source: a `tailnet_api_key` credential reads its devices, an import makes one host per device, and a probe checks reachability. An import never overwrites what a person set, and a removed host stays removed.

Evidence: migration 100, `embry0/services/hosts.py`.
