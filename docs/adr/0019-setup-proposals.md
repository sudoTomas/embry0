# ADR 0019: The onboarding agent proposes; a person applies

Status: accepted (2026-10-19)

Every write to a repository's test setup or preferences, from any path, appends a `repo_config_revisions` row holding the stored document before and after, in the write's own transaction: a write that cannot be recorded does not happen. Undo reverses only the newest change of a surface, and only while the stored document still equals that change's result; the undo is recorded too. In propose mode the onboarding agent drafts and smoke-tests a test setup but stores it in `repo_setup_proposals`, never in `repo_qa_configs`, so nothing it drafts takes effect until a person applies it. A proposal keeps the setup's `updated_at` from when its run started, so an apply is a compare-and-set that refuses a setup changed since. This narrows nothing in [ADR 0001](0001-langgraph-agent-sdk-dind-sandboxes.md): the agent runs in its sandbox, as every onboard run does.

Evidence: migrations 104 and 105; `embry0/storage/repositories/repo_config_revisions.py`, `embry0/storage/repositories/repo_setup_proposals.py`, `embry0/api/v1/repo_history.py`, `tests/unit/workflows/onboard/test_propose_mode.py`.
