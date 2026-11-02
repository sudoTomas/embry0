# Claude CLI Session File Layout (External Contract)

> **Verified on claude-code 2.1.316** (2026-11-02, #288): the CLI bundled
> in `claude-agent-sdk` 0.2.176, the version `uv.lock` pins and every agent
> runs. First verified on 2.1.292 (2026-10-08, #501). A CLI upgrade is an
> SDK upgrade, which is a lock change. When upgrading, check the layout
> below on the new CLI (`tests/integration/test_claude_cli_session_layout.py`
> is still a scaffold). If it moved, update this doc +
> `claude_cli_session.py`'s discovery hierarchy.

## What is this?

The Claude CLI (bundled in the `claude-agent-sdk` wheel the sandbox images
pin) writes per-session conversation state to disk as JSONL. embry0's
conversation-continuity layer (Plan C) needs to:

1. **Discover** session files at end-of-run, to capture conversation state.
2. **Restore** session blobs at start-of-resume, by writing them to the path
   the CLI will look in.

Both operations route through `embry0/agents/claude_cli_session.py` —
the single source of truth.

## Current on-disk layout (claude-code 2.1.316; unchanged since 2.1.92)

```
~/.claude/
└── projects/
    └── <sanitized-cwd>/
        └── <session_id>.jsonl
```

- `<sanitized-cwd>` = the working directory at session creation time,
  with `/` replaced by `-`. E.g., `/workspace/macro-lab` →
  `-workspace-macro-lab`.
- `<session_id>` = the UUID-style session identifier returned by the CLI
  in the first `SystemMessage` (also accessible via `--print-session-id`
  or as `result.session_id` in the SDK).
- File contents = JSONL, one event per line (user message, assistant
  message, tool calls, etc.).

## Request records (claude-code 2.1.316)

2.1.316 writes four entry types 2.1.292 did not: one `api-request-shape`
per session, an `api-request` and an `api-request-blob` per model request,
and `tools-used`. Discovery, restore and resume ignore them; the path and
the `cost-state` line below are unchanged.

They make the file larger, and the whole file is the resume blob, which
`MAX_RESUME_BLOB_BYTES` (5 MiB, `embry0/agents/session.py`) caps. Above
it a resume skips the session file (`resume_skipped_blob_too_large`) and
falls back to a bounded transcript replay. Measured offline against a
scripted model, the same runs on both CLIs:

| Run | 2.1.292 | 2.1.316 |
|---|---|---|
| 11 model requests, small tool results | 185,845 bytes | 325,724 bytes |
| 9 model requests, eight 20,000-line Bash outputs | 460,011 bytes | 612,816 bytes |

`api-request-shape` is about 89 KB once per session; `api-request` and
`api-request-blob` add roughly 2.5 to 4.5 KB per model request. A long
session therefore reaches the cap sooner, by about 90 KB plus a few KB
per request.

## Saved cost totals (claude-code 2.1.277+)

At exit a headless run appends its session's running totals:

```json
{"type": "cost-state", "sessionId": "<id>", "totalCostUSD": 0.0045, "modelUsage": {...}}
```

A resume restores the last such line, so the resumed run's
`total_cost_usd` includes every earlier run of the session (measured on
2.1.292: 0.0045, then 0.009 for an identical second run).
`restored_cost_usd()` reads it, and `SdkAgentExecutor` subtracts it, so a
run reports only its own cost. The per-run `usage` token counts are not
restored.

## Legacy layout (pre-2.0 CLI)

```
~/.claude/
└── sessions/
    └── <session_id>.jsonl
```

The discovery function still falls back to this for environments running
older CLI versions. New writes go to the projects layout.

## Discovery hierarchy

`find_session_file(home_dir, session_id, project_cwd=None)` tries:

1. `~/.claude/projects/<sanitize(project_cwd)>/<session_id>.jsonl` — fastest, only when cwd known.
2. `~/.claude/projects/*/<session_id>.jsonl` — glob fallback, when cwd unknown.
3. `~/.claude/sessions/<session_id>.jsonl` — legacy layout.

## When to update this doc

- The CLI moves the file (e.g., `chats/`, `conversations/`).
- The sanitization rule changes (e.g., URL-encoding instead of dash-replacement).
- The file format changes (e.g., binary instead of JSONL).

In all cases, update `claude_cli_session.py` first; update this doc to
match; rerun the integration test.
