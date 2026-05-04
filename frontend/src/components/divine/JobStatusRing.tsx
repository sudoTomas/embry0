import type { CardinalStage } from "@/lib/sigils";

interface JobStatusRingProps {
  currentStage: CardinalStage | null;
  scanning?: boolean;
  size?: number;
  className?: string;
}

const PIPELINE_ORDER: readonly CardinalStage[] = ["triage", "develop", "validate", "qa"] as const;

const STAGE_LABEL: Record<CardinalStage, string> = {
  triage: "TRG",
  develop: "DEV",
  validate: "REV",
  qa: "QA",
};

const STAGE_ARC: Record<CardinalStage, string> = {
  triage: "M 10 32 A 22 22 0 0 1 32 10",
  develop: "M 32 10 A 22 22 0 0 1 54 32",
  validate: "M 54 32 A 22 22 0 0 1 32 54",
  qa: "M 32 54 A 22 22 0 0 1 10 32",
};

const RECENCY_OPACITY = ["1", "0.75", "0.4", "0.15"] as const;

function arcOpacity(arc: CardinalStage, current: CardinalStage | null): string {
  if (current === null) return "0.4";
  const currentIndex = PIPELINE_ORDER.indexOf(current);
  const arcIndex = PIPELINE_ORDER.indexOf(arc);
  const stepsBack = (currentIndex - arcIndex + PIPELINE_ORDER.length) % PIPELINE_ORDER.length;
  return RECENCY_OPACITY[stepsBack];
}

const ARC_TRANSITION_STYLE = { transition: "opacity 600ms ease-out" } as const;

/**
 * Dashboard primary status ring. Four cardinal quarter-arcs whose opacity
 * encodes pipeline recency. Cardinal dots pulse N→E→S→W when idle; the
 * equator scans top↔bottom when scanning. Arc opacity changes transition
 * smoothly over 600ms (the Stage Shift effect).
 *
 * See `docs/superpowers/specs/2026-05-04-divine-animations-design.md`.
 */
export function JobStatusRing({
  currentStage,
  scanning = false,
  size = 120,
  className,
}: JobStatusRingProps) {
  const idle = currentStage === null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`divine-element text-primary ${className ?? ""}`}
      role="img"
      aria-label={currentStage ? `Pipeline stage: ${currentStage}` : "Pipeline idle"}
    >
      <line
        x1="14"
        y1="32"
        x2="50"
        y2="32"
        stroke="currentColor"
        strokeWidth="0.6"
        opacity="0.3"
        className={scanning ? "divine-equator-scan" : undefined}
      />
      {PIPELINE_ORDER.map((stage) => (
        <path
          key={stage}
          data-arc={stage}
          d={STAGE_ARC[stage]}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          opacity={arcOpacity(stage, currentStage)}
          strokeLinecap="round"
          style={ARC_TRANSITION_STYLE}
        />
      ))}
      <circle
        cx="32"
        cy="10"
        r="2.4"
        fill="currentColor"
        className={idle ? "divine-cardinal-pulse-n" : undefined}
      />
      <circle
        cx="54"
        cy="32"
        r="2.4"
        fill="currentColor"
        className={idle ? "divine-cardinal-pulse-e" : undefined}
      />
      <circle
        cx="32"
        cy="54"
        r="2.4"
        fill="currentColor"
        className={idle ? "divine-cardinal-pulse-s" : undefined}
      />
      <circle
        cx="10"
        cy="32"
        r="2.4"
        fill="currentColor"
        className={idle ? "divine-cardinal-pulse-w" : undefined}
      />
      <text
        x="32"
        y="35"
        textAnchor="middle"
        fontSize="6"
        fill="currentColor"
        fontFamily="ui-monospace, monospace"
        letterSpacing="0.1"
      >
        {currentStage ? STAGE_LABEL[currentStage] : ""}
      </text>
    </svg>
  );
}
