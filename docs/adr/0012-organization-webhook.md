# ADR 0012: One organization webhook replaces a webhook per repository

Status: accepted (2026-10-16)

The organization is the unit embry0 maintains (ADR 0010), yet intake still needed one webhook per repository. An organization now carries one GitHub webhook of its own, installed with `POST /api/v1/orgs/{owner}/webhook` only when the probe found the credential manages hooks, pointing at `<WEBHOOK_PUBLIC_URL>/api/v1/webhook` and signed with the GitHub webhook secret. The receiver's gate is additive: a delivery for an unregistered repository under a registered, active GitHub organization is recorded on that repository's fact row, which the needs list reads as `not_registered`, and is then handled exactly as before. With the organization's `auto_register` setting on (off by default) the repository is first registered and bound to the organization's credential. The gate never stops or fails a delivery.

Motivation: under an owner with many repositories, few end up with a repository webhook, even when the credential in use could install one on the organization ([ADR 0010](0010-organizations.md)).
