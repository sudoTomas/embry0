# ADR 0014: A standard is a schema embry0 ships and values the organization holds

Status: accepted (2026-10-18)

Organizations that hold their interfaces to a design standard name the same things: token roles (a page background, one accent, a body font, a table row height), a type scale, component rules. They differ in what fills those roles. So a standard is stored in two halves: the schema, the roles, scale and rules a kind of standard names, and the values, one organization's fill, where every value names a role the schema declares. embry0 ships the vocabulary, the validation and the audit, and no organization's values: those are configuration, versioned in `standards`, where a write supersedes the active version.

The owner of a standard is the forge owner, the key organizations already use (ADR 0010): the repositories a standard governs are the ones under that owner, a boundary the forge already draws. Each repository starts in mode off, and a repository in mode off is never audited.

Evidence: migration 102, `embry0/services/standards.py`.
