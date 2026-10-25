/**
 * The achievement diary's icons (spec 2026-10-25): original 12×12 pixel
 * glyphs drawn here, one per kind of deed. Each row is a string: `o` the
 * outline, `#` the tier's colour, `+` a highlight, `-` a shade of the tier
 * colour, `.` nothing. The tier colour comes in as `color`, so a Bronze anvil
 * and a Rune anvil are the same drawing in two metals.
 */
import { ICONS, lift } from "@/components/progression/achievementIconData";
import { OUTLINE } from "@/components/roster/appearance/palette";

/** One icon as crisp pixels: ``color`` is the tier colour, lifted a touch; an unknown name draws the star. */
export function AchievementIcon({
  name,
  color,
  size = 36,
  title,
}: {
  name: string | null;
  color: string;
  size?: number;
  title?: string;
}) {
  const rows = ICONS[name ?? "secret"] ?? ICONS.star;
  // The metal, lifted a little: mithril and adamant are dark enough to vanish on the dark panels.
  const body = lift(color);
  const pixels: React.ReactNode[] = [];
  rows.forEach((row, y) => {
    [...row].forEach((ch, x) => {
      if (ch === ".") return;
      const key = `${x}-${y}`;
      if (ch === "o") pixels.push(<rect key={key} x={x} y={y} width={1} height={1} fill={OUTLINE} />);
      else if (ch === "#") pixels.push(<rect key={key} x={x} y={y} width={1} height={1} fill={body} />);
      else if (ch === "+")
        pixels.push(
          <g key={key}>
            <rect x={x} y={y} width={1} height={1} fill={body} />
            <rect x={x} y={y} width={1} height={1} fill="#ffffff" opacity={0.55} />
          </g>,
        );
      else if (ch === "-")
        pixels.push(
          <g key={key}>
            <rect x={x} y={y} width={1} height={1} fill={body} />
            <rect x={x} y={y} width={1} height={1} fill="#000000" opacity={0.35} />
          </g>,
        );
    });
  });
  return (
    <svg
      viewBox="0 0 12 12"
      width={size}
      height={size}
      shapeRendering="crispEdges"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      data-icon={name ?? "secret"}
    >
      {pixels}
    </svg>
  );
}
