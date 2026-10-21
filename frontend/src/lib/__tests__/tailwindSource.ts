/**
 * Tailwind writes a rule only for a class it finds written out whole in the
 * source text (index.css names no @source: the build scans the project). A
 * class put together at runtime, `${bg}/15`, is in no file, so it gets no rule
 * and the element draws without it. jsdom applies no Tailwind, so a test asks
 * the source instead.
 */
const SOURCES = Object.values(
  import.meta.glob(
    ["/src/**/*.{ts,tsx}", "!/src/**/__tests__/**", "!/src/**/*.test.{ts,tsx}", "!/src/test-setup.ts"],
    { query: "?raw", import: "default", eager: true },
  ) as Record<string, string>,
);

/**
 * Whether `cls` is written whole, as a class in a string, in an app source
 * file. Test files are left out: the build scans them too, and a class that
 * exists only because a test spells it would hide the bug it pins.
 */
export function inSource(cls: string): boolean {
  const escaped = cls.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  const whole = new RegExp(`(^|[\\s"'\`])${escaped}(?=[\\s"'\`]|$)`);
  return SOURCES.some((text) => whole.test(text));
}
