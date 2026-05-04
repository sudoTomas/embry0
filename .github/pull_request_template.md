<!--
  Athanor PR Template — high-standard contribution loop.

  Required sections:  Summary, Motivation, How it works, Verification.
  Required for spec-backed PRs:  Spec + Plan links.
  Required when adopting an external pattern:  Pattern provenance.

  Delete unused sections rather than leaving them empty.
-->

## Summary

<!-- One or two sentences. What changed and why, in plain language. -->

## Motivation — Intent + Problem

<!--
What problem does this solve?  Who hurts?  Why now?  What outcome do we want?
Lead with the user-facing reason; the technical reason is secondary.
-->

## How it works

<!--
Walk a reader through the mechanism — do not restate the diff.  Cover:

  - The data path (what flows where)
  - The state machine or contract touched (if any)
  - The assumptions that hold for this to work
  - Any external library used + why it was chosen over alternatives

A reader should leave knowing how the piece works without opening the code.
-->

## Pattern provenance

<!--
For pattern imports (companion, collaborator, other internal tools), cite the source:

  "Concept ported from companion-dashboard/server.mjs:441 (proposals CRUD).
  No code copied; design re-implemented for Athanor's React + LangGraph stack."

For original work, write "N/A — originated in this PR".
-->

## Verification

<!-- Mirror the spec's verification block. -->

**Automated**

- [ ] `cd frontend && npm run lint` clean
- [ ] `cd frontend && npm run test` all passing
- [ ] `cd frontend && npm run build` succeeds (type-check + bundle)
- [ ] `pytest tests/ -v` green (only if backend touched)

**Manual**

- [ ] Walked the happy path end-to-end in a browser
- [ ] Walked the failure / empty / edge-case paths
- [ ] Verified `prefers-reduced-motion` behavior (only if animations touched)
- [ ] Verified mobile viewport doesn't break (only if UI touched)

## Screenshots

<!-- For UI changes: before / after.  Skip section for backend-only PRs. -->

## Out of scope / follow-ups

<!--
Explicit list of things deliberately left out so the reviewer doesn't
think they were forgotten.  One line per item.
-->

## Spec + Plan

<!-- Required when this PR implements a spec.  Both files live in docs/superpowers/. -->

- Spec: `docs/superpowers/specs/...`
- Plan: `docs/superpowers/plans/...`

## Anti-goals respected

<!-- Tick the rows this PR honors.  Delete rows that don't apply. -->

- [ ] Purely additive — no existing code removed without explicit follow-up
- [ ] No identity drift — Athanor stays gold-primary + cyan-ring; red is destructive-only
- [ ] No new npm/Python dependency (or, if added: list package + rationale below)
- [ ] No silent default flip — existing user behavior preserved
- [ ] Divine layer escape hatches respected (if applicable): `prefers-reduced-motion` + `body[data-divine="off"]`
- [ ] No secrets in diff
- [ ] No emoji in code

## Checklist

- [ ] PR title follows Conventional Commits — `type(scope): short subject`
- [ ] Each commit is one concept, individually meaningful
- [ ] Spec status set to `Accepted` if PR ships the implementation
- [ ] Linked spec/plan files exist in this PR
