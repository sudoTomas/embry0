import { Link } from "react-router";
import { layerName, policyLayerPath, type PolicyLayer } from "@/lib/settingsLayer";

/**
 * Where an organization's or a repository's own operator policy is
 * changed: its layer of the settings document, on the Policy tab with that
 * layer picked. Its other settings stay on its own page.
 */
export function PolicyLayerLink({ layer }: { layer: Exclude<PolicyLayer, { layer: "deployment" }> }) {
  const name = layerName(layer);
  return (
    <p className="text-sm text-fg-secondary [overflow-wrap:anywhere]" data-testid="policy-layer-link">
      {`The operator policy ${name} sets over the deployment's is its layer of the settings document: `}
      <Link to={policyLayerPath(layer)} className="text-fg hover:text-primary hover:underline">
        open it on the Policy tab
      </Link>
      .
    </p>
  );
}
