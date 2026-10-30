import { Link } from "react-router";
import { layerName, layerOfScope, policyLayerPath } from "@/lib/settingsLayer";
import type { LayersRefused } from "@/lib/types/settings";

/**
 * The layer values a change to the document under them makes read as
 * inherited (`layers_refused`): each one's layer, its key and why it is
 * refused. A deployment's change is never refused because of a layer; the
 * layers' values give way instead, and this says which. The server names at
 * most 100 and counts them all. Once the change is stored (`linked`), each
 * layer links to open it; a preview's do not, since leaving would drop the
 * edit it previews.
 */
export function LayersRefusedList({
  result,
  intro,
  testId,
  linked = false,
}: {
  result: LayersRefused;
  intro: string;
  testId: string;
  linked?: boolean;
}) {
  const refused = result.layers_refused ?? [];
  if (refused.length === 0) return null;
  const more = Math.max((result.layers_refused_total ?? refused.length) - refused.length, 0);
  return (
    <div
      role="alert"
      data-testid={testId}
      className="space-y-1 rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning"
    >
      <p className="font-semibold">{intro}</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {refused.map((refusal, index) => (
          <li key={index} data-testid="layers-refused-entry">
            {linked ? (
              <Link to={policyLayerPath(layerOfScope(refusal.scope))} className="underline hover:text-fg">
                {layerName(refusal.scope)}
              </Link>
            ) : (
              layerName(refusal.scope)
            )}
            {": "}
            <code className="font-mono">{refusal.key}</code>
            {` (${refusal.reason})`}
          </li>
        ))}
      </ul>
      {more > 0 && <p>{`And ${more} more.`}</p>}
      <p className="text-xs">Each reads the value it inherits until its layer is changed to fit.</p>
    </div>
  );
}
