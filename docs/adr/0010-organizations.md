# ADR 0010: The organization is the unit embry0 maintains

Status: accepted (2026-10-16)

embry0 attends one repository at a time. Intake by webhook or tracker, log watching and QA are each switched on per repository, so a forge owner with dozens of repositories is dozens of separate decisions, and in practice most are never taken: a deployment can attend one repository closely and leave the rest of the same owner untouched, with a credential that could have reached all of them. The limit was not access. It was that the owner was not a thing embry0 modelled.

An organization is now a row: the forge owner (a GitHub organization or user, or a GitLab group), the credential embry0 acts with there, and what the probe learned that credential can do, by calling the forge. Discovery enumerates the owner's repositories into facts, the needs list tells a person what embry0 still needs to maintain each one, and the actions (register, hook, onboard, watch) run from those facts, idempotently. Standards are to be keyed by owner too (spec slice 8).

Two rules follow. The probe only trusts real calls: a repository's `permissions` block reflects the user, not the token's grant, so it is never read, and a push grant is proven only by writing a ref, which stays a separate explicit action. And registering an owner changes nothing for any repository until a person acts on the needs list; the organization row is a lens, not a switch.

Spec: `docs/superpowers/specs/2026-10-16-organizations.md`.
