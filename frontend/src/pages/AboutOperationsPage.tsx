import { PatentCanvas } from "@/components/divine/patents/PatentCanvas";
import { PatentFigure } from "@/components/divine/patents/PatentFigure";
import { FullGeodesicSphere } from "@/components/divine/patents/FullGeodesicSphere";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import {
  OPERATIONS,
  OPERATION_ELEMENT,
  OPERATION_NUMERAL,
  type Operation,
} from "@/components/divine/operations";

const OPERATION_ROLE: Record<Operation, string> = {
  calcinate: "the matter is broken down to ash",
  dissolve: "the structure gives way to water",
  separate: "pure is divided from impure",
  conjoin: "opposites enter the sacred marriage",
  ferment: "the work decays, then is reborn",
  distill: "the essence rises, refined",
  coagulate: "the gold is sealed in stone",
};

/**
 * Lore page at /about/operations — the magnum opus rendered in seven
 * patent cards plus a hero. Hand-authored static SVG, gold on dark,
 * patent-drawing chrome.
 *
 * See `docs/superpowers/specs/2026-05-04-patent-drawing-layer-design.md`.
 */
export function AboutOperationsPage() {
  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6 animate-fade-up">
      {/* Hero */}
      <PatentCanvas
        date="MAY 04, 2026"
        inventor="O. BODART · EMBRY0 INC."
        patentNo="EMBRY0-2026-001"
        title="Geodesic Identity"
        elements="FIRE · WATER · AIR · EARTH · AETHER · STONE"
        epigraph='"As above, so below."'
      >
        <div className="flex justify-center py-2">
          <PatentFigure number="I" caption="Full geodesic, four cardinals">
            <FullGeodesicSphere size={260} />
          </PatentFigure>
        </div>
      </PatentCanvas>

      {/* Seven operations */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {OPERATIONS.map((op) => (
          <PatentCanvas
            key={op}
            density="compact"
            patentNo={`OP · ${OPERATION_NUMERAL[op]}`}
            inventor={op.toUpperCase()}
            elements={OPERATION_ELEMENT[op].toUpperCase()}
            epigraph={`OP ${OPERATION_NUMERAL[op]}/VII`}
          >
            <div className="flex flex-col items-center gap-2 py-2">
              <PatentFigure number={OPERATION_NUMERAL[op]} caption={op} inset>
                <OperationGlyph operation={op} size={100} titled />
              </PatentFigure>
              <div className="text-[10px] tracking-[0.12em] uppercase mt-1 opacity-65 italic max-w-[180px] text-center">
                {OPERATION_ROLE[op]}
              </div>
            </div>
          </PatentCanvas>
        ))}
      </div>

      {/* Footer note */}
      <div className="text-[10px] tracking-[0.18em] uppercase opacity-40 text-center pt-4 font-mono">
        embry0 — the alchemical furnace where code transmutes
      </div>
    </div>
  );
}
