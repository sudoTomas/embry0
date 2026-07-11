# Claude CLI Session File Layout (External Contract)

> **Pinned to claude-code 2.1.92** (as of 2026-05). When upgrading the
> CLI, run `tests/integration/test_claude_cli_session_layout.py` (Task 7
> of this plan). If it fails, update this doc + `claude_cli_session.py`'s
> discovery hierarchy.

## What is this?

The Claude CLI (bundled into the `@anthropic-ai/claude-code` npm package)
writes per-session conversation state to disk as JSONL. embry0's
conversation-continuity layer (Plan C) needs to:

1. **Discover** session files at end-of-run, to capture conversation state.
2. **Restore** session blobs at start-of-resume, by writing them to the path
   the CLI will look in.

Both operations route through `embry0/agents/claude_cli_session.py` —
the single source of truth.

## Current on-disk layout (claude-code 2.1.92)

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
