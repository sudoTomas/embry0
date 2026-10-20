import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Sprite } from "@/components/roster";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
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
      <AgentAvatar identity={preview} size={48} className="mt-6" />
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
            <Label>Avatar</Label>
            <div role="radiogroup" aria-label="Avatar" className="mt-2 grid grid-cols-8 gap-1.5">
              {AVATARS.map((id) => {
                const selected = value.avatar === id;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    aria-label={id}
                    title={id}
                    onClick={() => onChange({ avatar: id })}
                    className={cn(
                      "flex items-center justify-center rounded-md border p-1",
                      selected ? "border-white bg-white/[0.06]" : "border-white/10 hover:border-white/30",
                    )}
                  >
                    <Sprite avatar={id} color={value.color} state="idle" size={32} decorative />
                  </button>
                );
              })}
            </div>
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
