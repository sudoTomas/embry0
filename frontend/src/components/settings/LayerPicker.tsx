import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { useOrganizations } from "@/hooks/useOrgs";
import { useRegisteredRepos } from "@/hooks/useRepos";
import { useSettingsLayers } from "@/hooks/useSettingsDocument";
import { layerFromKey, layerKey, layerName, layerOptions, type PolicyLayer } from "@/lib/settingsLayer";

/** What the picked document is, in one sentence under the picker. */
function describe(layer: PolicyLayer): string {
  if (layer.layer === "organization") {
    return `${layerName(layer)}'s layer: what every repository under ${layerName(layer)} reads over the deployment's document, unless the repository sets its own.`;
  }
  if (layer.layer === "repository") {
    return `${layerName(layer)}'s layer: what this repository reads over its organization's layer and the deployment's document.`;
  }
  return "The deployment's document: every key's value for every organization and repository that does not set its own.";
}

/**
 * Which settings document the Policy tab works on: the deployment's, an
 * organization's layer or a repository's. It offers every registered
 * organization and repository the lists show, and every stored layer, each
 * marked when a layer is stored; picking one that has none opens an empty
 * layer, which its first save creates. The lists are only for choosing: if
 * one fails to load, the others and the stored layers are still offered.
 */
export function LayerPicker({ layer, onPick }: { layer: PolicyLayer; onPick: (layer: PolicyLayer) => void }) {
  const layers = useSettingsLayers();
  const organizations = useOrganizations();
  const repositories = useRegisteredRepos();
  const options = layerOptions(
    layers.data ?? [],
    (organizations.data ?? []).map((org) => org.owner),
    (repositories.data ?? []).map((repo) => repo.repo),
    layer,
  );
  const failed = [
    layers.isError && "the stored layers",
    organizations.isError && "the organizations",
    repositories.isError && "the repositories",
  ].filter(Boolean);

  return (
    <div className="space-y-1" data-testid="policy-layer">
      <Label htmlFor="policy-layer-picker">Layer</Label>
      <Select
        id="policy-layer-picker"
        value={layerKey(layer)}
        onChange={(e) => onPick(layerFromKey(e.target.value))}
        className="max-w-full sm:max-w-md"
        data-testid="policy-layer-picker"
      >
        <option value="deployment">Deployment</option>
        {options.organizations.length > 0 && (
          <optgroup label="Organizations">
            {options.organizations.map((option) => (
              <option key={option.key} value={option.key} data-stored={option.stored ? "true" : "false"}>
                {option.label}
              </option>
            ))}
          </optgroup>
        )}
        {options.repositories.length > 0 && (
          <optgroup label="Repositories">
            {options.repositories.map((option) => (
              <option key={option.key} value={option.key} data-stored={option.stored ? "true" : "false"}>
                {option.label}
              </option>
            ))}
          </optgroup>
        )}
      </Select>
      <p className="text-xs text-fg-muted [overflow-wrap:anywhere]" data-testid="policy-layer-description">
        {describe(layer)}
      </p>
      {failed.length > 0 && (
        <p className="text-xs text-warning" data-testid="policy-layer-lists-failed">
          {`Could not load ${failed.join(", ")}, so the list may be short. A layer can still be opened by its link.`}
        </p>
      )}
    </div>
  );
}
