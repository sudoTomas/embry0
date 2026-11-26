/**
 * Every drawing the roster is made of, by the name appearance/SOURCES.md records it under (#178): the body kit's
 * (parts.ts) and the equipment catalogue's (items.ts). shapes.test.ts walks what the kit and the catalogue can draw
 * and holds this list to it, and SOURCES.md's rows to this list.
 */
import { ITEM_SHAPES } from "./items.ts";
import { BODY_SHAPES } from "./parts.ts";

export const SHAPES: Readonly<Record<string, unknown>> = { ...BODY_SHAPES, ...ITEM_SHAPES };
