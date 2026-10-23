# ADR 0015: What a repository takes to maintain is read from its facts by rules

Status: accepted (2026-10-18)

Each row of `GET /api/v1/orgs/{owner}/repos` carries `maintain_hint`, one sentence on what embry0 needs to maintain that repository, and `maintain_steps`, the needs actions the sentence names in the order they are taken: register, credential, hook, onboard, watch, setting. Both come from one table of rules in `embry0/services/org_maintain.py` over the row's facts and the organization's probe. No model is called, so the same facts always give the same sentence, and a fact the credential could not see is never a yes or a no. The rules mirror the needs kinds ([ADR 0011](0011-organization-needs.md)), so a repository's steps are the actions of the needs that cover it; the one refinement is that a missing webhook asks for the credential when the probe found no hook administration. A model may later write a narrative over these facts; it does not replace them.

Evidence: `tests/unit/services/test_org_maintain.py` pins one fact fixture per rule, the step order, and that the steps equal the needs list's actions for every repository.
