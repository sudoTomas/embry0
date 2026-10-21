# ADR 0001: LangGraph + Claude Agent SDK, agents execute in DinD sandboxes

Status: accepted (2026-04-04)

The orchestrator is a LangGraph `StateGraph` checkpointed to PostgreSQL; agent turns run through the Claude Agent SDK by default (the Claude CLI `execution_mode` and the direct xAI executor are the sanctioned exceptions, each behind explicit config); and every agent executes inside an ephemeral Docker-in-Docker container rather than on the orchestrator host. Customer code is cloned inside the sandbox and deleted on teardown — there are no shared workspace volumes and no retained checkout on the host.

No single commit carries this decision; the date is the day the last of the three pieces landed. LangGraph arrived 2026-04-02 (`3b4e02c` AsyncPostgresSaver, `464d0ee` issue-to-PR StateGraph), the DinD-aware Docker client and sandbox manager the same day (`2d55bf0`, `d7d9d1e`), and `6de5c2d` (2026-04-04) switched triage from a bespoke loop to the Claude Agent SDK with OAuth. [`docs/architecture.md`](../architecture.md) states the retention rule: "Customer code never persists on the host."

The retention clause did not hold: job events, saved agent sessions and pre-built QA images keep code on the host. [ADR 0017](0017-code-retention.md) records what is kept and how to bound it.
