# QA empty selection and forge-neutral report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A QA run that selects no app is visible on the stream and in the log, fails with `ERR_QA_NO_APPS` when it had no change context, and the QA report step writes its sticky comment on GitLab as well as GitHub.

**Architecture:** Four independent tasks on the existing QA orchestrator. Task 1 makes provider validation fail-closed (prefix contract). Task 2 adds the error code and rewrites the orchestrator's empty-selection branch, with the docs. Task 3 adds a sticky issue-note upsert to `GitLabClient`. Task 4 stores the forge on QA state and makes `qa_report_node` dispatch on it. Tasks 1 to 4 share no interface except the method signature Task 4 calls on Task 3's client, fixed below.

**Tech Stack:** Python 3.12, pydantic v2, structlog, httpx (`MockTransport` in tests), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-18-qa-empty-selection-and-forge-report.md`

## Global Constraints

- Every new test is shown failing on the pre-fix tree first, with its own assertion message (CLAUDE.md, "Prove a check can fail").
- No customer, deployment, host, job id or cost in code, tests, docs or commit messages (public repository, name-denylist CI).
- Vendor-neutral naming: nothing named after the deployment that reported the issue.
- Log event names are `snake_case` structlog events; no f-strings inside `logger.*` calls.
- `ruff check`, `ruff format --check` and `mypy embry0` stay clean.
- Commits follow `.claude/skills/commit-standard` (Conventional Commits, no AI co-author footers) and end with the session trailer the dispatch gives.
- Run tests with the worktree's own `.venv` and `PYTHONPATH=<worktree>`; verify `embry0.__file__` points at the worktree when in doubt.

## Review Focus

1. A provider that returns a message with neither prefix: the run must fail, not pass. Pinned in Task 1.
2. A standalone run on the default branch (empty diff) with `qa_required: auto` and only prefixed static apps: must fail with `ERR_QA_NO_APPS`, never `completed`. Pinned in Task 2.
3. A pull request whose diff touched no declared app: must still pass, and the comment must say why. Pinned in Task 2.
4. A GitLab deployment's standalone QA job (no issue number): the report node must not look up any token and must log `qa_report_skipped_no_change_request`. Pinned in Task 4.
5. The GitLab upsert must update the earliest marked note, not append a second one. Pinned in Task 3.

---

### Task 1: Provider validation messages are classified fail-closed

**Files:**
- Modify: `embry0/workspace_providers/static_apps.py:106-118` (`validate`)
- Modify: `embry0/workspace_providers/provider.py:105-110` (docstring)
- Modify: `embry0/workflows/qa/orchestrator_helpers.py:79-100` (`validate_against_qa_config`)
- Test: `tests/unit/workspace_providers/test_static_apps.py`
- Test: `tests/unit/workflows/qa/test_orchestrator.py`

**Interfaces:**
- Consumes: `WorkspaceProvider.validate(apps_declared_in_qa_config: list[str]) -> list[str]`.
- Produces: the contract "every message starts with `error:` or `warning:`"; `validate_against_qa_config` returns `(errors, warnings)` where an unprefixed message lands in `errors`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/workspace_providers/test_static_apps.py`, replace `test_validate_flags_undeclared_qa_yaml_apps` and `test_validate_empty_config_errors` with:

```python
def test_validate_flags_undeclared_qa_yaml_apps_as_errors():
    messages = _p(CFG).validate(["hub", "ghost"])
    assert len(messages) == 1
    assert "ghost" in messages[0]
    assert messages[0].startswith("error: "), f"unprefixed message is dropped by the orchestrator: {messages[0]!r}"


def test_validate_empty_config_is_an_error():
    messages = _p({}).validate(["hub"])
    assert messages and "no apps declared" in messages[0]
    assert messages[0].startswith("error: "), f"unprefixed message is dropped by the orchestrator: {messages[0]!r}"
```

In `tests/unit/workflows/qa/test_orchestrator.py`, after `test_validate_against_qa_config_separates_warnings_from_errors`:

```python
def test_validate_against_qa_config_treats_unprefixed_message_as_error():
    """A provider that forgets the prefix must fail the run, not be ignored."""
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=["qa.yaml app 'hub' is not declared in the provider config"],
    )
    errors, warnings = validate_against_qa_config(provider, cfg)
    assert errors == ["qa.yaml app 'hub' is not declared in the provider config"], "unprefixed message was dropped"
    assert warnings == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workspace_providers/test_static_apps.py tests/unit/workflows/qa/test_orchestrator.py -k "unprefixed or validate_flags or validate_empty" -q`
Expected: 3 failed, each with its own assertion message.

- [ ] **Step 3: Implement**

`static_apps.py`, `validate`:

```python
    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        if not self.config.apps:
            return [
                "error: static-apps provider has no apps declared — set "
                "workspace_provider.config.apps to the runnable app names"
            ]
        declared = set(self.config.apps)
        return [
            f"error: qa.yaml app {name!r} is not declared in the static-apps provider config "
            f"(declared: {sorted(declared)}) — the apps: keys must be the provider's app names"
            for name in apps_declared_in_qa_config
            if name not in declared
        ]
```

`provider.py`, `validate` docstring:

```python
        """Cross-check the user's qa.yaml `apps:` keys against reality.

        Returns a list of human-readable strings, each starting with
        ``error:`` or ``warning:`` (case-insensitive). Empty list means
        clean. The orchestrator fails the run on errors, shows warnings in
        the QA comment, and treats a message with neither prefix as an
        error so a provider that forgets the prefix cannot be ignored.
        """
```

`orchestrator_helpers.py`, `validate_against_qa_config`, replace the two comprehensions:

```python
    messages = provider.validate(list(config.apps.keys()) if app_names is None else list(app_names))
    errors: list[str] = []
    warnings: list[str] = []
    for message in messages:
        lowered = message.lower()
        if lowered.startswith("error:"):
            errors.append(message)
        elif lowered.startswith("warning:"):
            warnings.append(message)
        else:
            # Fail closed: a message the provider did not classify used to be
            # dropped here, which let a misconfigured repository pass QA with
            # nothing selected (#65).
            logger.warning(
                "qa_validate_message_unprefixed",
                provider=getattr(provider, "name", type(provider).__name__),
                message=message,
            )
            errors.append(message)
    return errors, warnings
```

Update the docstring's "Return (errors, warnings)" paragraph to mention the fail-closed rule.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workspace_providers tests/unit/workflows/qa/test_orchestrator.py -q`
Expected: all pass (read the summary line).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0
git add embry0/workspace_providers/static_apps.py embry0/workspace_providers/provider.py embry0/workflows/qa/orchestrator_helpers.py tests/unit/workspace_providers/test_static_apps.py tests/unit/workflows/qa/test_orchestrator.py
git commit -m "fix(qa): classify provider validation messages fail-closed"
```

---

### Task 2: An empty app selection is visible, and fails without a change context

**Files:**
- Modify: `embry0/safety/error_codes.py:117-124` (enum) and `:229-236` (categories)
- Modify: `embry0/notifications/public_errors.py:63` (public text)
- Modify: `embry0/workflows/qa/orchestrator.py:588-602` (`qa_required: never` branch) and `:803-811` (`if not apps:` branch)
- Modify: `docs/architecture.md:1372` (error-code table)
- Modify: `docs/qa-yaml-reference.md:46-48`, `:85-97`, `:525-527`
- Test: `tests/unit/safety/test_failure_taxonomy.py`
- Test: `tests/unit/workflows/qa/test_orchestrator.py`
- Test: `tests/unit/workflows/qa/test_orchestrator_force_all_apps.py`

**Interfaces:**
- Consumes: `events.qa_apps_resolved(apps=[])`, `events.progress(message=, node=)`, the node's existing `writer` (line 477, `_stream_writer()`), `OrchestratorOutcome`.
- Produces: `ErrorCode.QA_NO_APPS` with value `"ERR_QA_NO_APPS"`; `qa_state["error_code"]`, `qa_state["error_message"]` on the failing path (read by `embry0/services/_executor_run.py:1550-1577`, unchanged).

- [ ] **Step 1: Write the failing tests**

`tests/unit/safety/test_failure_taxonomy.py`: add to the `test_standalone_qa_outcomes` parametrize list, before the `({}, ErrorCode.QA_NO_VERDICT)` row:

```python
        ({"qa_error_code": "ERR_QA_NO_APPS"}, ErrorCode.QA_NO_APPS),
```

and a new test next to it:

```python
def test_no_apps_is_a_configuration_error_with_public_text():
    from embry0.notifications.public_errors import public_error_message
    from embry0.safety.error_codes import ERROR_CATEGORIES, FailureCategory

    assert ERROR_CATEGORIES[ErrorCode.QA_NO_APPS] is FailureCategory.CONFIG
    assert "no app" in public_error_message("ERR_QA_NO_APPS")
```

(Check the real name of the category enum and its CONFIG member in `error_codes.py` and use those.)

`tests/unit/workflows/qa/test_orchestrator.py`: replace `test_orchestrator_node_short_circuits_when_no_apps_affected` with two tests. Use the same `fake_provider` construction and `monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", ...)` as the test it replaces, and capture events with `monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)` the way the tests near line 2671 do (`orch` is `embry0.workflows.qa.orchestrator`).

```python
@pytest.mark.asyncio
async def test_orchestrator_node_fails_when_no_apps_and_no_change_context(monkeypatch):
    """Empty diff + empty selection is the vacuous pass from #65: it must fail."""
    ...  # fake_provider with an empty affected set, as before
    events_out: list[dict] = []
    monkeypatch.setattr(orch, "get_stream_writer", lambda: events_out.append)
    state = {
        "job_id": "run-2",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {"qa_yaml_v2_raw": _QA_YAML_V2, "changed_files": []},
    }
    out = await qa_orchestrator_node(state, {"configurable": {"qa_app_results_repo": repo_mock}})
    qa = out["qa"]
    assert qa["final_status"] == "failed", "a run that selected no app and had no change context passed"
    assert qa["outcome"]["overall_status"] == "failed"
    assert qa["error_code"] == "ERR_QA_NO_APPS"
    assert "force_all_apps" in qa["error_message"]
    assert qa["apps_to_qa"] == []
    assert [e for e in events_out if e["type"] == "qa_apps_resolved"] == [
        {**[e for e in events_out if e["type"] == "qa_apps_resolved"][0], "apps": []}
    ], "qa_apps_resolved was not emitted for the empty selection"
    assert any(e["type"] == "progress" and "no app" in e["message"] for e in events_out)
    assert repo_mock.upsert_with_boot_phase.await_count == 0


@pytest.mark.asyncio
async def test_orchestrator_node_passes_with_reason_when_diff_touched_no_declared_app(monkeypatch):
    ...  # same fake_provider; changed_files non-empty
    state["qa"]["changed_files"] = ["docs/readme.md"]
    out = await qa_orchestrator_node(state, config)
    qa = out["qa"]
    assert qa["final_status"] == "passed"
    assert qa["outcome"]["overall_status"] == "passed"
    assert any("touched no declared app" in w for w in qa["outcome"]["validation_warnings"]), (
        "the pass carries no reason"
    )
    assert any(e["type"] == "qa_apps_resolved" and e["apps"] == [] for e in events_out)
```

Extend `test_orchestrator_node_qa_required_never_short_circuits`: capture events the same way and add

```python
    assert any(e["type"] == "qa_apps_resolved" and e["apps"] == [] for e in events_out), (
        "qa_required: never left the stream silent"
    )
```

`tests/unit/workflows/qa/test_orchestrator_force_all_apps.py`, `test_qa_required_auto_with_no_diff_still_short_circuits`: keep the "fan-out must not happen" assertion, rename to `test_qa_required_auto_with_no_diff_fails_with_no_apps`, and change the two final assertions to

```python
    assert qa["apps_to_qa"] == []
    assert qa["final_status"] == "failed", "auto + empty diff + empty selection must not pass"
    assert qa["error_code"] == "ERR_QA_NO_APPS"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/safety/test_failure_taxonomy.py tests/unit/workflows/qa/test_orchestrator.py tests/unit/workflows/qa/test_orchestrator_force_all_apps.py -k "no_apps or no_change_context or touched_no_declared or qa_required_never or no_diff" -q`
Expected: the new and changed tests fail with their messages (the enum member does not exist yet; the orchestrator still returns passed).

- [ ] **Step 3: Implement**

`error_codes.py`, enum, after `QA_CONFIG_INVALID`:

```python
    QA_NO_APPS = "ERR_QA_NO_APPS"  # QA selected no app: the diff was empty and no declared app is always on (#65)
```

categories, after the `QA_CONFIG_INVALID` row: `ErrorCode.QA_NO_APPS: _C.CONFIG,`

`public_errors.py`, after the `QA_CONFIG_INVALID` line:

```python
    ErrorCode.QA_NO_APPS: "QA selected no app to run: the branch has no changes against its base and no app is always on",
```

`orchestrator.py`, `qa_required: never` branch (line 588): before `return {"qa": qa_state}`, add

```python
        writer(events.qa_apps_resolved(apps=[]))
        writer(events.progress(message="QA skipped: qa_required is never", node="orchestrate_qa"))
```

(`writer` is the node's stream writer; check its name in `_qa_orchestrator_node_impl` around line 546 and use that variable.)

`orchestrator.py`, replace the `if not apps:` branch (line 803) with:

```python
    if not apps:
        declared = sorted(cfg.apps.keys())
        provider_affected = sorted(affected_managed) if not force_all_apps else []
        reason = "no_change_context" if not changed_files_str else "diff_touched_no_declared_app"
        logger.info(
            "qa_no_apps_to_qa",
            parent_run_id=state.get("job_id"),
            reason=reason,
            declared_apps=declared,
            provider_affected=provider_affected,
            n_changed_files=len(changed_files_str),
        )
        writer(events.qa_apps_resolved(apps=[]))
        if reason == "no_change_context":
            msg = (
                "QA selected no app: the diff against the base branch is empty and no declared app is "
                f"always on (declared: {declared}; provider-affected: {provider_affected}). "
                "Set qa_required: always, pass force_all_apps on the job, or declare an app without prefixes."
            )
            writer(events.progress(message=msg, node="orchestrate_qa"))
            outcome = OrchestratorOutcome(
                overall_status="failed",
                apps_to_qa=[],
                failure_summary=msg,
                validation_warnings=warnings,
            )
            qa_state["outcome"] = _outcome_to_dict(outcome)
            qa_state["per_app_results"] = []
            qa_state["error_code"] = ErrorCode.QA_NO_APPS.value
            qa_state["error_message"] = msg
            qa_state["final_status"] = "failed"
            return {"qa": qa_state}
        msg = (
            f"QA selected no app: the change touched no declared app (declared: {declared}; "
            f"provider-affected: {provider_affected}; changed files: {len(changed_files_str)})."
        )
        writer(events.progress(message=msg, node="orchestrate_qa"))
        outcome = OrchestratorOutcome(
            overall_status="passed",
            apps_to_qa=[],
            validation_warnings=[*warnings, msg],
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["per_app_results"] = []
        qa_state["final_status"] = "passed"
        return {"qa": qa_state}
```

`affected_managed` is only bound in the `else` branch above; bind `affected_managed: list[str] = []` before the `if force_all_apps:` at line 675 so both paths define it. Import `ErrorCode` from `embry0.safety.error_codes` at module top (check for an existing import first).

`docs/architecture.md`, after the `ERR_QA_CONFIG_INVALID` row:

```
| `ERR_QA_NO_APPS` | A QA run selected no app and had no change context: the diff was empty and no declared app is always on. Opt in with `qa_required: always`, `force_all_apps`, or an app without `prefixes`. |
```

`docs/qa-yaml-reference.md`:
- Step 4: replace "(any affected app **not** declared under `apps:` is silently skipped)" with "(an affected app **not** declared under `apps:` is skipped, with a line in the QA comment and a `qa_no_apps_to_qa` log entry when nothing is left; a run with an empty diff that selects nothing fails with `ERR_QA_NO_APPS`)".
- `static-apps` section, after the YAML block: one sentence: "The `apps:` keys must be the names declared here (`hub`, `leads` above). A key the provider does not declare fails the run with `ERR_QA_CONFIG_INVALID`."
- Gotchas, the `app_name` bullet: replace "an affected app you didn't declare is silently skipped" with "an affected app you didn't declare is skipped and named in the comment; when that leaves nothing on a run with no diff, the run fails with `ERR_QA_NO_APPS`".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/safety tests/unit/workflows/qa tests/unit/notifications tests/unit/services/test_failure_code_wiring.py -q`
Expected: all pass (read the summary line). Also run `tests/unit/docs` if it exists.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0
git add embry0/safety/error_codes.py embry0/notifications/public_errors.py embry0/workflows/qa/orchestrator.py docs/architecture.md docs/qa-yaml-reference.md tests/unit/safety/test_failure_taxonomy.py tests/unit/workflows/qa/test_orchestrator.py tests/unit/workflows/qa/test_orchestrator_force_all_apps.py
git commit -m "fix(qa): fail a run that selects no app without a change context"
```

---

### Task 3: GitLabClient gains a sticky issue-note upsert

**Files:**
- Modify: `embry0/execution/gitlab_client.py:133-141` (after `create_issue_note`)
- Test: `tests/unit/execution/test_gitlab_client.py`

**Interfaces:**
- Consumes: `ForgeClient.paginate`, `.put`, `.post` (`post` accepts `retry_transient`), `project_path`.
- Produces: `async def upsert_sticky_issue_note(self, repo: str, iid: int, body: str, *, marker: str) -> int` — Task 4 calls it with `marker=COMMENT_MARKER` from `embry0.notifications.qa_comment`.

- [ ] **Step 1: Write the failing tests**

After `test_create_issue_note_reports_failure`:

```python
async def test_upsert_sticky_issue_note_creates_when_no_marked_note():
    import json as json_mod

    calls: list[tuple[str, str, dict | None]] = []

    def handler(request):
        body = json_mod.loads(request.content) if request.content else None
        calls.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": "unrelated"}])
        return httpx.Response(201, json={"id": 7})

    note_id = await _client(handler).upsert_sticky_issue_note("g/p", 11, "<!-- m -->new", marker="<!-- m -->")
    assert note_id == 7
    assert [c[0] for c in calls] == ["GET", "POST"], f"expected a lookup then a create, got {calls}"
    assert calls[1][1].endswith("/projects/g%2Fp/issues/11/notes")
    assert calls[1][2] == {"body": "<!-- m -->new"}


async def test_upsert_sticky_issue_note_updates_the_earliest_marked_note():
    import json as json_mod

    calls: list[tuple[str, str, dict | None]] = []

    def handler(request):
        body = json_mod.loads(request.content) if request.content else None
        calls.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "body": "<!-- m -->old"}, {"id": 9, "body": "<!-- m -->dup"}])
        return httpx.Response(200, json={"id": 3})

    note_id = await _client(handler).upsert_sticky_issue_note("g/p", 11, "<!-- m -->new", marker="<!-- m -->")
    assert note_id == 3
    assert [c[0] for c in calls] == ["GET", "PUT"], f"expected a lookup then an update, got {calls}"
    assert calls[1][1].endswith("/projects/g%2Fp/issues/11/notes/3")
    assert calls[1][2] == {"body": "<!-- m -->new"}


async def test_upsert_sticky_issue_note_lists_notes_oldest_first():
    def handler(request):
        if request.method == "GET":
            assert request.url.params["sort"] == "asc", "notes must be listed oldest first"
            assert request.url.params["order_by"] == "created_at"
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    assert await _client(handler).upsert_sticky_issue_note("g/p", 11, "x", marker="m") == 1


async def test_upsert_sticky_issue_note_raises_on_rejected_write():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).upsert_sticky_issue_note("g/p", 11, "x", marker="m")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_gitlab_client.py -k upsert_sticky -q`
Expected: 4 failed with `AttributeError: ... has no attribute 'upsert_sticky_issue_note'`.

- [ ] **Step 3: Implement**

In `GitLabClient`, after `create_issue_note`:

```python
    async def upsert_sticky_issue_note(self, repo: str, iid: int, body: str, *, marker: str) -> int:
        """Create or update the one note on an issue whose body carries ``marker``.

        The GitLab twin of ``embry0.notifications.qa_comment.upsert_sticky_comment``:
        the earliest marked note is updated (later duplicates are left alone), and
        a missing one is created. Returns the note id; raises ``HTTPStatusError``
        when the write is rejected.
        """
        path = self.project_path(repo, f"/issues/{iid}/notes")
        notes = await self.paginate(
            path, repo=repo, params={"sort": "asc", "order_by": "created_at"}, per_page=100, max_pages=20
        )
        existing = next(
            (int(note["id"]) for note in notes if isinstance(note, dict) and marker in (note.get("body") or "")),
            None,
        )
        if existing is not None:
            resp = await self.put(f"{path}/{existing}", repo=repo, json={"body": body})
            resp.raise_for_status()
            return existing
        # Sent once: a note GitLab accepted and answered 502 must not be posted twice.
        resp = await self.post(path, repo=repo, json={"body": body}, retry_transient=False)
        resp.raise_for_status()
        return int(resp.json()["id"])
```

Check `paginate` merges `params` with `per_page` (it does: `merged = dict(params or {})`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/execution/test_gitlab_client.py tests/unit/execution/test_gitlab_client_pr_review.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0
git add embry0/execution/gitlab_client.py tests/unit/execution/test_gitlab_client.py
git commit -m "feat(gitlab): upsert a sticky note on an issue"
```

---

### Task 4: The QA report step dispatches on the forge

**Files:**
- Modify: `embry0/workflows/qa/orchestrator.py:445-452` (`_init_orchestrator_node_impl` output)
- Modify: `embry0/workflows/qa/orchestrator_report.py:90-165` (`qa_report_node`) and its module docstring
- Test: `tests/unit/workflows/qa/test_orchestrator_report.py`
- Test: `tests/unit/workflows/qa/test_orchestrator.py` (an existing `init_orchestrator_node` test gains one assertion)

**Interfaces:**
- Consumes: `resolve_forge(repos_repo, repo, job_id=)` from `embry0.services.forge_resolver`; `make_gitlab_client(config, credential_resolver)` from `embry0.execution.gitlab_client`; `GitLabClient.upsert_sticky_issue_note(repo, iid, body, *, marker)` (Task 3; in this task's tests the client is a fake, so Task 3 need not be present); `COMMENT_MARKER` from `embry0.notifications.qa_comment`; configurable keys `repos_repo`, `embry0_config`, `credential_resolver`, `github_token` (all set by `embry0/services/issue_executor.py:329-368`).
- Produces: `state["qa"]["forge"]` (`"github"` or `"gitlab"`), written by the init node.

- [ ] **Step 1: Write the failing tests**

`tests/unit/workflows/qa/test_orchestrator_report.py`, after the existing tests (reuse `_state()`; `structlog.testing.capture_logs` for log names):

```python
@pytest.mark.asyncio
async def test_standalone_job_without_change_request_skips_before_any_token_lookup(monkeypatch):
    import structlog.testing

    async def no_lookup(*a, **kw):
        raise AssertionError("token lookup must not happen without an issue number")

    monkeypatch.setattr("embry0.services.credentials.resolver.forge_token", no_lookup)
    written_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    state = _state(forge="gitlab")
    state["issue_number"] = None
    with structlog.testing.capture_logs() as logs:
        out = await qa_report_node(state, {"configurable": {"github_token": "tok"}})
    assert out == {}
    written_comment.assert_not_awaited()
    assert any(log["event"] == "qa_report_skipped_no_change_request" for log in logs), [log["event"] for log in logs]


@pytest.mark.asyncio
async def test_gitlab_forge_writes_a_sticky_issue_note(monkeypatch):
    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(return_value=5)
    made: dict = {}

    def fake_make(config, credential_resolver=None, **kw):
        made["config"] = config
        made["resolver"] = credential_resolver
        return fake_client

    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", fake_make)
    github_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", github_comment)

    sentinel_config, sentinel_resolver = object(), object()
    out = await qa_report_node(
        _state(forge="gitlab"),
        {"configurable": {"embry0_config": sentinel_config, "credential_resolver": sentinel_resolver}},
    )
    assert out == {}
    github_comment.assert_not_awaited()
    fake_client.upsert_sticky_issue_note.assert_awaited_once()
    kwargs = fake_client.upsert_sticky_issue_note.await_args
    assert kwargs.args[:2] == ("org/repo", 42) or (kwargs.kwargs.get("repo"), kwargs.kwargs.get("iid")) == ("org/repo", 42)
    assert "hub" in (kwargs.args[2] if len(kwargs.args) > 2 else kwargs.kwargs["body"])
    assert made["config"] is sentinel_config and made["resolver"] is sentinel_resolver


@pytest.mark.asyncio
async def test_legacy_state_without_forge_resolves_it_from_the_registry(monkeypatch):
    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(return_value=5)
    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", AsyncMock())

    async def resolve(repos_repo, repo, *, job_id=""):
        assert repos_repo == "the-registry"
        return "gitlab"

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.resolve_forge", resolve)
    state = _state()
    assert "forge" not in state["qa"]
    await qa_report_node(state, {"configurable": {"repos_repo": "the-registry"}})
    fake_client.upsert_sticky_issue_note.assert_awaited_once()


@pytest.mark.asyncio
async def test_gitlab_note_failure_is_logged_not_raised(monkeypatch):
    import structlog.testing

    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(side_effect=RuntimeError("gitlab 502"))
    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", lambda *a, **kw: fake_client)
    with structlog.testing.capture_logs() as logs:
        out = await qa_report_node(_state(forge="gitlab"), {"configurable": {}})
    assert out == {}
    assert any(log["event"] == "qa_comment_upsert_failed" and log.get("forge") == "gitlab" for log in logs)


@pytest.mark.asyncio
async def test_github_without_token_logs_a_forge_neutral_skip(monkeypatch):
    import structlog.testing

    written_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    with structlog.testing.capture_logs() as logs:
        await qa_report_node(_state(forge="github"), {"configurable": {}})
    written_comment.assert_not_awaited()
    skip = [log for log in logs if log["event"] == "qa_report_skipped_no_forge_token"]
    assert skip and skip[0]["forge"] == "github", [log["event"] for log in logs]
```

Note: `_state(forge="gitlab")` relies on `_state(**qa_overrides)` merging into `qa`, which it already does.

`tests/unit/workflows/qa/test_orchestrator.py`: find the `init_orchestrator_node` test that drives the clone with a `repos_repo` (or the one closest to it; `grep -n "init_orchestrator_node" tests/unit/workflows/qa/test_orchestrator.py tests/unit/workflows/qa/test_orchestrator_base_branch.py`), and add

```python
    assert out["qa"]["forge"] == "github", "init_orchestrator did not publish the forge for the report node"
```

(or `"gitlab"` if that test's registry fake answers gitlab). If no existing test reaches the output block with a registry fake, add a minimal one that does, modelled on the nearest test.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/workflows/qa/test_orchestrator_report.py tests/unit/workflows/qa/test_orchestrator.py -k "forge or change_request or gitlab" -q`
Expected: the new tests fail (no `resolve_forge` attribute on the module, GitHub path still taken, `forge` key missing).

- [ ] **Step 3: Implement**

`orchestrator.py`, in the output block of `_init_orchestrator_node_impl` (after `qa["changed_files"] = list(changed_files)`):

```python
    # #65: the report node dispatches on the forge this node already resolved
    # for the clone, instead of assuming GitHub.
    qa["forge"] = forge
```

Confirm `forge` is bound on every path that reaches this block (it is assigned at line 183 before the clone; if an earlier early-return exists between them, leave it, since those returns never reach the output block).

`orchestrator_report.py`: add module-level imports `from embry0.services.forge_resolver import resolve_forge` and `from embry0.notifications.qa_comment import COMMENT_MARKER, render_qa_comment, upsert_sticky_comment`. Rewrite the body of `qa_report_node` from `repo = state.get("repo")` down to the GitHub write as:

```python
    repo = state.get("repo")
    issue_number = state.get("issue_number")
    run_number = int(state.get("attempt_number") or 1)
    overall_status = outcome.get("overall_status") or "infra_error"
    failure_summary = outcome.get("failure_summary")

    if issue_number is None or not repo:
        # A standalone QA job has no issue or pull request to comment on;
        # the dashboard and the job row carry its verdict.
        logger.info("qa_report_skipped_no_change_request", repo=repo)
        writer(events.node_completed(node="qa_report"))
        return {}

    resolver = configurable.get("credential_resolver")
    forge = qa_state.get("forge")
    if not forge:
        try:
            forge = await resolve_forge(configurable.get("repos_repo"), repo, job_id=str(state.get("job_id") or ""))
        except Exception:
            logger.warning("qa_report_forge_lookup_failed", repo=repo, exc_info=True)
            forge = "github"

    body = render_qa_comment(
        run_number=run_number,
        overall_status=overall_status,
        apps_to_qa=apps_to_qa,
        results=_hydrate_results(per_app),
        failure_summary=failure_summary,
        validation_warnings=validation_warnings,
    )

    if forge == "gitlab":
        from embry0.execution.gitlab_client import make_gitlab_client

        try:
            client = make_gitlab_client(configurable.get("embry0_config"), credential_resolver=resolver)
            await client.upsert_sticky_issue_note(repo, int(issue_number), body, marker=COMMENT_MARKER)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa_comment_upsert_failed", forge="gitlab", repo=repo, issue_number=issue_number, error=str(exc))
        writer(events.node_completed(node="qa_report"))
        return {}

    default_token = configurable.get("github_token")
    from embry0.services.credentials.resolver import forge_token

    try:
        github_token = await forge_token(resolver, repo, "github", default_token or "") or default_token
    except Exception:
        logger.warning("qa_report_token_lookup_failed", repo=repo, exc_info=True)
        github_token = ""
    if not github_token:
        logger.warning("qa_report_skipped_no_forge_token", forge="github", repo=repo)
        writer(events.node_completed(node="qa_report"))
        return {}

    try:
        await upsert_sticky_comment(
            github_token=github_token,
            repo=repo,
            issue_number=issue_number,
            body=body,
            client=GitHubClient(default_token=default_token or "", credential_resolver=resolver),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("qa_comment_upsert_failed", forge="github", repo=repo, issue_number=issue_number, error=str(exc))

    writer(events.node_completed(node="qa_report"))
    return {}
```

Keep the `make_gitlab_client` import inside the function so tests can monkeypatch `embry0.execution.gitlab_client.make_gitlab_client`. Update the module docstring: the node writes the sticky comment on the forge the init node resolved (`state['qa']['forge']`), GitHub issue comment or GitLab issue note; a job with no issue number skips. Existing tests that patch `forge_token` on the resolver module or set `GITHUB_TOKEN__<OWNER>` keep working because the GitHub path is unchanged in substance.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/workflows/qa tests/unit/workflows/issue_to_pr/test_verify_node.py -q`
Expected: all pass (read the summary line).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check embry0 tests && .venv/bin/ruff format --check embry0 tests && .venv/bin/mypy embry0
git add embry0/workflows/qa/orchestrator.py embry0/workflows/qa/orchestrator_report.py tests/unit/workflows/qa/test_orchestrator_report.py tests/unit/workflows/qa/test_orchestrator.py
git commit -m "fix(qa): write the QA report on the job's forge, not only GitHub"
```

---

## Integration (controller)

Tasks 1 to 4 run in parallel worktrees branched from the plan branch. Merge order: 1, 3, 2, 4 (2 and 4 both edit `orchestrator.py` in distinct regions; 1 and 2 both edit `test_orchestrator.py`). After merging: grep for conflict markers, grep the touched files for duplicate `def`s, run `ruff`, `mypy`, the full unit suite against a throwaway Postgres, gitleaks over the branch, and the name-denylist grep. Then one whole-branch review, one fix wave, PR.

## Deploy notes

The change is orchestrator-side only (`embry0/workflows/qa/`, `embry0/execution/gitlab_client.py`, `embry0/safety/`, `embry0/notifications/`): `infra/deploy.sh` rebuilds the orchestrator and MCP images; no sandbox image rebuild, no migration. After deploying, a deployment whose stored QA config names the app differently in the provider block and the `apps:` block will see its QA jobs fail with `ERR_QA_CONFIG_INVALID` until the config is corrected (`PUT /repos/{owner}/{repo}/qa-config`).
