/**
 * The sprite states (spec 3.2): what an agent is doing, and how many frames
 * each state steps between. Every avatar is composed from an appearance
 * (appearance/); the twelve 16x16 drawings this module held became composed
 * people in the progression spec (2026-10-24), so they can wear what they
 * earn. Not part of the divine layer (divine/CLAUDE.md rule 9): sprites may
 * sit on live cards and lists and step no faster than two frames per second.
 */

export type SpriteState = "idle" | "thinking" | "tool" | "done" | "failed" | "pending";

export const SPRITE_STATES: readonly SpriteState[] = ["idle", "thinking", "tool", "done", "failed", "pending"];
export const FRAME_COUNTS: Record<SpriteState, 1 | 2> = { idle: 2, thinking: 2, tool: 2, done: 1, failed: 1, pending: 1 };
