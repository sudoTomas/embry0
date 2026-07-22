# repo-configs/ — external per-repo QA config store (EMB-48, Phase 1)

Per-repo qa.yaml v2 files that live on the embry0 side instead of in the
target repo. Everything here except this README is **gitignored** — QA
criteria, internal URLs, and credentials hints never enter the public repo.

## Layout

```
repo-configs/
  <owner>__<repo>/
    qa.yaml        # schema v2 — same contract as an in-repo .embry0/qa.yaml
```

Example: the config for `raven-cargo/ai-quoting` lives at
`repo-configs/raven-cargo__ai-quoting/qa.yaml`.

## Precedence

When a file exists here for a repo it **replaces** the repo's in-tree
`.embry0/qa.yaml` entirely — no merge, exactly one source of truth per
repo. When absent, the in-repo file is used, so existing integrations run
unchanged. Full contract: [`docs/qa-yaml-reference.md`](../docs/qa-yaml-reference.md).

## Wiring

Compose mounts this directory read-only into the orchestrator at
`/data/repo-configs` and sets `EMBRY0_QA_CONFIG_DIR` to point at it
(`infra/docker-compose.yml`). Edits are picked up on the next QA run — no
restart needed. Outside compose, set `EMBRY0_QA_CONFIG_DIR` yourself; unset
means the store is disabled.

## Writing configs

Three ways a config lands here:

1. **Onboarding agent (EMB-50):** `embry0 onboard <owner/repo>` — analyzes
   the repo, drafts qa.yaml v2, validates it (schema + boot/ready smoke),
   and writes it here on success.
2. **API:** `PUT /api/v1/repos/{owner}/{repo}/qa-config` with the raw YAML
   body — schema-validated before the write. `GET`/`DELETE` round it out.
3. **By hand:** drop the file in the layout above. No validation until the
   next QA run — prefer the API.

Phase 2 (planned) moves the store into Postgres with a dashboard editor;
this directory then becomes seed/backup.
