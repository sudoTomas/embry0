# Divine Layer — Hard Rules

The components in this directory render embry0's alchemical/hermetic identity. The intent is **atmospheric**, not decorative. The user must never feel they're using a "themed" tool; they feel they're operating an **instrument**.

When editing files in this directory or adding new divine elements, every one of these rules is a hard constraint.

---

## 1. Pure SVG only

No PNG, no font glyphs, no Lottie, no Canvas. SVG paths so the layer renders crisp at any DPI and inherits color from CSS. Total bytes for the entire divine layer must stay under ~5KB minified.

## 2. `currentColor` for stroke; no hardcoded colors

Every sigil and every mark stroke uses `stroke="currentColor"` (or `fill="currentColor"` for filled paths). The renderer's text color drives it. This guarantees the layer respects the user's theme tokens (`--color-primary`, stage tokens, etc.) and never introduces a hardcoded color outside the existing palette.

## 3. Animation is suppressed by default

- All animations gated by `@media (prefers-reduced-motion: no-preference)`.
- All animations gated by `body:not([data-divine="off"])`.
- **One animated property per element** is the default. Compound-property animations are allowed only when explicitly justified in the spec, and only in three narrow shapes:
  1. **One-shot animations** (rule's spirit is "no constant restless motion"; one-shots are temporal events, not motion).
  2. **Narrow-range opacity composed with another property**, where the opacity range is narrow enough (≤0.7 span) that the visual reads as a single composed motion.
  3. **Dissipation motion** — `r` (or equivalent size) + `opacity` together, where the visual reads as a single coherent expand-and-fade gesture (e.g. a ring dissolving outward). Justification: opacity is bound mechanically to the size — an expanding ring without fading would be visually wrong; the two properties are one motion, not two.
- Current explicit exceptions: `<DivineRipple>` (one-shot `r` + `opacity`), `divine-equator-scan` (narrow opacity + transform), `divine-op-dissolve-ring` (dissipation), `divine-op-distill-*` (dissipation x3), `divine-op-ferment-core` (narrow opacity + transform-scale). Each exception is justified in the docstring of the component (or the CSS comment of the keyframe) that owns it.
- No animation longer than 4 seconds; no animation that loops faster than 2 seconds. (One-shots are exempt from the loop-floor rule by definition.)

## 4. Escape hatch

`<body data-divine="off">` removes the entire layer:
- All `.divine-element` SVGs and components are hidden via CSS (see `frontend/src/index.css`).
- No animations run.
- No layout shifts when toggling.

This attribute is the v1 opt-out. A Settings UI for it can come later. The contract is: anyone who can edit one HTML attribute can disable the entire layer.

## 5. Operator-critical paths skip divine flourishes

These surfaces never render a divine element:
- Error pages and error toasts (PageError, sonner toasts)
- Destructive confirmations (delete dialogs, cancel-job confirmations)
- Latency-sensitive lists (job feeds, log tails — anything that re-renders >1Hz)
- Form fields and form validation feedback

If you're adding a new divine element to a surface, ask: "Does this surface ever surface failure, danger, or speed-critical info?" If yes, do not add the element.

## 6. Never combine more than one divine element per component instance

Bad: an `<Embry0Mark />` next to an `<AlchemicalSigil />` next to yet another glyph in the same row. The cumulative effect tips into "themed".

Good: one divine element per layout region. The TopBar carries `<Embry0Mark />` and nothing else divine. The Pipeline Editor agent nodes each carry one `<AlchemicalSigil />` and nothing else divine.

## 7. Hermetic copy register stays close to operator copy

When re-wording empty states or error messages with hermetic language:
- Keep the line count
- Keep approximate line length
- Stay legible to a non-Hermetic user (do not use Latin without a translation, do not rely on knowledge of alchemical history)
- One notch more dignified, not lore-dump

Pattern (good): "Drop agents here" → "The vessel is empty. Drag an agent to begin."
Pattern (bad): "Drop agents here" → "PRIMA MATERIA: Place the seven agents in the order of their elemental affinity to begin the Great Work."

## 8. New divine elements require a written justification

Any addition (new sigil, new component, new animation) lands with a short written justification — in the PR description and in the docstring of the new element — even if it's a one-line CSS change. The discipline keeps the layer from accumulating flourishes by drift.

---

These rules exist because the layer's value is in restraint. A divine element that breaks a rule erodes the whole layer's credibility.
