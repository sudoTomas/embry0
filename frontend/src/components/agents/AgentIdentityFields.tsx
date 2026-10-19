import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import {
  AVATARS,
  DISPLAY_NAME_MAX,
  PALETTE,
  PALETTE_HEX,
  TAGLINE_MAX,
  identityOf,
  type AvatarId,
  type PaletteId,
} from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";

export interface IdentityFieldsValue {
  display_name: string;
  tagline: string;
  avatar: AvatarId;
  color: PaletteId;
}

interface AgentIdentityFieldsProps {
  /** The agent's slug, for the preview's fallback name. */
  type: string;
  value: IdentityFieldsValue;
  onChange: (patch: Partial<IdentityFieldsValue>) => void;
}

/** Display name, tagline, avatar and colour of an agent, with a live tile. */
export function AgentIdentityFields({ type, value, onChange }: AgentIdentityFieldsProps) {
  const preview = identityOf({ type: type || "agent", ...value });
  return (
    <div className="flex gap-4">
      <AgentAvatar identity={preview} size={40} className="mt-6" />
      <div className="flex-1 space-y-4">
        <div>
          <div className="flex items-baseline justify-between">
            <Label htmlFor="agent-display-name">Display name</Label>
            <span className="text-[10px] text-white/30">
              {value.display_name.length}/{DISPLAY_NAME_MAX}
            </span>
          </div>
          <Input
            id="agent-display-name"
            maxLength={DISPLAY_NAME_MAX}
            value={value.display_name}
            onChange={(e) => onChange({ display_name: e.target.value })}
            className="mt-1"
          />
        </div>
        <div>
          <Label htmlFor="agent-tagline">Tagline</Label>
          <Input
            id="agent-tagline"
            maxLength={TAGLINE_MAX}
            value={value.tagline}
            onChange={(e) => onChange({ tagline: e.target.value })}
            className="mt-1"
          />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label htmlFor="agent-avatar">Avatar</Label>
            <Select
              id="agent-avatar"
              value={value.avatar}
              onChange={(e) => onChange({ avatar: e.target.value as AvatarId })}
              className="mt-1"
            >
              {AVATARS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Color</Label>
            <div role="radiogroup" aria-label="Color" className="mt-2 flex flex-wrap gap-2">
              {PALETTE.map((id) => {
                const selected = value.color === id;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    aria-label={id}
                    onClick={() => onChange({ color: id })}
                    style={{ background: PALETTE_HEX[id] }}
                    className={cn("h-7 w-7 rounded-full border-2", selected ? "border-white" : "border-transparent")}
                  />
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
