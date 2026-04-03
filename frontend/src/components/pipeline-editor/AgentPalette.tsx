import { useState } from "react";
import { useAgentTypes } from "@/hooks/useAgentTypes";
import { getAgentColor, getAgentCategory } from "@/lib/graph-utils";
import type { AgentTypeInfo } from "@/lib/types";

export function AgentPalette() {
  const { data: agents } = useAgentTypes();
  const [search, setSearch] = useState("");

  const filtered = (agents ?? []).filter(
    (a) =>
      a.type.toLowerCase().includes(search.toLowerCase()) ||
      a.description.toLowerCase().includes(search.toLowerCase()),
  );

  const grouped = filtered.reduce<Record<string, AgentTypeInfo[]>>(
    (acc, agent) => {
      const cat = getAgentCategory(agent.type);
      if (!acc[cat]) acc[cat] = [];
      acc[cat]!.push(agent);
      return acc;
    },
    {},
  );

  const onDragStart = (event: React.DragEvent, agentType: string) => {
    event.dataTransfer.setData("application/agentType", agentType);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="w-[200px] bg-[#161822] border-r border-white/[0.08] p-3 overflow-y-auto shrink-0">
      <div className="text-[11px] uppercase text-white/40 tracking-wider mb-2">
        Agent Palette
      </div>
      <input
        type="text"
        placeholder="Search agents..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/60 placeholder:text-white/20 mb-3 outline-none focus:border-white/20 transition-colors"
      />
      {Object.entries(grouped).map(([category, items]) => (
        <div key={category}>
          <div className="text-[10px] uppercase text-white/30 mt-3 mb-1.5">
            {category}
          </div>
          {items.map((agent) => (
            <div
              key={agent.type}
              draggable
              onDragStart={(e) => onDragStart(e, agent.type)}
              className="bg-[#1e2030] border rounded-md px-2.5 py-2 mb-1.5 cursor-grab text-xs hover:bg-[#252840] transition-colors"
              style={{
                borderColor: `${getAgentColor(agent.type)}50`,
              }}
            >
              <div className="flex items-center gap-1.5">
                <span style={{ color: getAgentColor(agent.type) }}>●</span>
                <span className="font-medium text-white/80">{agent.type}</span>
              </div>
              <p className="text-[10px] text-white/25 mt-0.5 line-clamp-2 pl-4 leading-relaxed">
                {agent.description}
              </p>
            </div>
          ))}
        </div>
      ))}
      <div className="text-[10px] uppercase text-white/30 mt-3 mb-1.5">
        Custom
      </div>
      <div
        draggable
        onDragStart={(e) => onDragStart(e, "custom")}
        className="bg-[#1e2030] border border-dashed border-white/20 rounded-md px-2.5 py-2 cursor-grab text-xs hover:bg-[#252840]"
      >
        <span className="text-white/50">+</span> Custom Agent
      </div>
    </div>
  );
}
