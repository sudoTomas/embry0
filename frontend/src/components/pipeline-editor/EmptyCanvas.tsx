import { EmptyVesselGlyph } from "@/components/divine/EmptyVesselGlyph";

/**
 * Pipeline editor empty-canvas hero. Uses the calcinate operation to
 * read as "the matter is being broken down, the fire is laid, the work
 * begins" — the first operation of the magnum opus, the natural opening
 * gesture for an empty vessel.
 */
export function EmptyCanvas() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none z-10">
      <EmptyVesselGlyph
        operation="calcinate"
        size={72}
        copy="The vessel is empty"
        subCopy="Drag an agent from below to begin the work"
      />
    </div>
  );
}
