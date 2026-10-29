import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import type { Lead, Party, World, WorldCharacter } from "@/api/world";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { CharacterPanel } from "@/components/world/CharacterPanel";
import { GuildHall } from "@/components/world/GuildHall";
import { QuestBoard } from "@/components/world/QuestBoard";
import { QuestInbox } from "@/components/world/QuestInbox";
import { QuestScrollDialog } from "@/components/world/QuestScroll";
import { SendPartyDialog } from "@/components/world/SendPartyDialog";
import { WorldChat } from "@/components/world/WorldChat";
import { WorldMap } from "@/components/world/WorldMap";
import { WriteQuestDialog } from "@/components/world/WriteQuestDialog";
import { useArt } from "@/components/world/art/manifest";
import { NightfallFilter } from "@/components/world/art/Nightfall";
import "@/components/world/world.css";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useWorld } from "@/hooks/useWorld";
import { useWorldChatDock } from "@/hooks/useWorldChatDock";
import {
  inboxOrder,
  kindsById,
  levelUps,
  narrate,
  xpDrops,
  type NarrationLine,
} from "@/lib/world";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";

/** How long an experience drop floats before it clears. */
const XP_DROP_MS = 2_600;

/**
 * The home screen (spec 2026-10-25): the agents as a guild. The finished
 * jobs waiting for review; the hall's picture and every agent's card; the
 * leads to start; the map; the activity box. The frame speaks embry0's
 * operator words; the pictures and the activity box speak the game's. The
 * address keeps what is open (?character=, ?quest=, ?lead=, ?write=,
 * ?view=map), so a link can open a job's report.
 */
export function WorldPage() {
  const world = useWorld();
  const art = useArt();
  const lookup = useIdentityLookup();
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const [params, setParams] = useSearchParams();
  const previous = useRef<World | null>(null);
  const [lines, setLines] = useState<NarrationLine[]>([]);
  const [drops, setDrops] = useState<Map<string, number>>(new Map());
  const [ups, setUps] = useState<ReadonlySet<string>>(new Set());
  const [dock, setDock] = useWorldChatDock();

  useEffect(() => {
    if (!world.data) return;
    const before = previous.current;
    const said = narrate(
      before?.parties ?? null,
      world.data.parties,
      (agent) => lookup(agent).displayName,
    );
    const rose = levelUps(before?.characters ?? null, world.data.characters);
    for (const [agent, level] of rose)
      said.push({
        key: `${agent}:level:${level}`,
        text: `Congratulations, ${lookup(agent).displayName} reached level ${level}!`,
        tone: "win",
        at: new Date().toISOString(),
        agent,
      });
    if (said.length) setLines((l) => [...l, ...said].slice(-30));
    const gained = xpDrops(before?.characters ?? null, world.data.characters);
    previous.current = world.data;
    if (gained.size || rose.size) {
      setDrops(gained);
      setUps(new Set(rose.keys()));
      const timer = window.setTimeout(() => {
        setDrops(new Map());
        setUps(new Set());
      }, XP_DROP_MS);
      return () => window.clearTimeout(timer);
    }
  }, [world.data, lookup]);

  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    for (const k of ["character", "quest", "lead", "write"])
      if (k !== key && key !== "view") next.delete(k);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const skillName = useMemo(() => {
    const names = new Map(
      (world.data?.skills ?? []).map((s) => [s.id, s.name]),
    );
    return (stat: string) => names.get(stat) ?? stat;
  }, [world.data?.skills]);

  if (world.isError)
    return (
      <PageError
        message="The world did not load"
        onRetry={() => void world.refetch()}
      />
    );
  if (!world.data) return <Skeleton className="h-96 w-full" />;
  const data = world.data;
  const kinds = kindsById(data.lead_kinds);
  const partyOf = new Map<string, Party>(
    data.parties.map((p) => [p.job_id, p]),
  );
  const selectedCharacter: WorldCharacter | undefined = data.characters.find(
    (c) => c.agent_type === params.get("character"),
  );
  const selectedLead: Lead | undefined = data.quests.find(
    (q) => q.id === params.get("lead"),
  );
  const questId = params.get("quest");
  // The inbox in the order it shows: a verdict or a skip moves the report to the quest after this one.
  const reviewOrder = inboxOrder(data.inbox).map((q) => q.job_id);
  const reviewAt = questId ? reviewOrder.indexOf(questId) : -1;
  const nextQuest =
    reviewAt >= 0
      ? (reviewOrder[reviewAt + 1] ??
        reviewOrder.find((id) => id !== questId) ??
        null)
      : null;
  const view = params.get("view") === "map" ? "map" : "guild";
  const strongest = Math.max(1, ...data.characters.map((c) => c.level));
  const repos = [...new Set(data.regions.map((r) => r.repo))].sort();

  return (
    <Tabs
      defaultValue="guild"
      value={view}
      onValueChange={(v) => set("view", v === "map" ? "map" : null)}
    >
      <div className="space-y-4" data-testid="world-page">
        <NightfallFilter />
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="title-page text-xl font-bold">Guild</h1>
            <p
              className="text-sm text-fg-secondary"
              data-testid="world-summary"
            >
              {data.characters.length} agents ·{" "}
              {data.characters.filter((c) => c.state !== "idle").length} busy ·{" "}
              {data.quests.length} quests on offer · account total{" "}
              {data.account.total_level.toLocaleString("en-US")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <TabsList>
              <TabsTrigger value="guild">Hall</TabsTrigger>
              <TabsTrigger value="map">Map</TabsTrigger>
            </TabsList>
            {dock === "closed" && (
              <Button
                variant="outline"
                onClick={() => setDock("open")}
                data-testid="show-activity"
              >
                Activity
              </Button>
            )}
            <Button disabled={!isAdmin} onClick={() => set("write", "1")}>
              New task
            </Button>
          </div>
        </header>

        <QuestInbox
          quests={data.inbox}
          canAct={isAdmin}
          onOpen={(jobId) => set("quest", jobId)}
        />

        <TabsContent value="map" className="mt-0">
          <WorldMap
            art={art}
            regions={data.regions}
            quests={data.quests}
            parties={data.parties}
            characters={data.characters}
            dungeons={data.dungeons ?? []}
            onPickLead={(lead) => set("lead", lead.id)}
            onPickCharacter={(agent) => set("character", agent)}
          />
        </TabsContent>
        <TabsContent value="guild" className="mt-0">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
            <GuildHall
              art={art}
              characters={data.characters}
              parties={data.parties}
              loot={data.loot}
              skillName={skillName}
              xpDrops={drops}
              levelUps={ups}
              selected={selectedCharacter?.agent_type ?? null}
              onSelect={(agent) => set("character", agent)}
              onBoard={() =>
                document
                  .getElementById("quest-board-title")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
              onLoot={(jobId) => set("quest", jobId)}
            />
            <div className="lg:sticky lg:top-3 lg:max-h-[calc(100vh-6rem)]">
              <QuestBoard
                quests={data.quests}
                kinds={kinds}
                partyLevel={strongest}
                onPick={(lead) => set("lead", lead.id)}
              />
            </div>
          </div>
        </TabsContent>

        {dock !== "closed" && (
          <div className="sticky bottom-2 z-10 max-w-md">
            <WorldChat lines={lines} dock={dock} onDock={setDock} />
          </div>
        )}

        {selectedCharacter && (
          <CharacterPanel
            character={selectedCharacter}
            party={
              selectedCharacter.job_id
                ? partyOf.get(selectedCharacter.job_id)
                : undefined
            }
            skillName={skillName}
            canAct={isAdmin}
            onClose={() => set("character", null)}
            onOpenQuest={(jobId) => set("quest", jobId)}
            onWriteQuest={(agent) => {
              const next = new URLSearchParams(params);
              next.delete("character");
              next.set("write", agent);
              setParams(next, { replace: true });
            }}
          />
        )}
        {selectedLead && (
          <SendPartyDialog
            lead={selectedLead}
            kinds={kinds}
            characters={data.characters}
            by={by}
            canSend={isAdmin}
            onClose={() => set("lead", null)}
            onSent={() => set("lead", null)}
          />
        )}
        {questId && (
          <QuestScrollDialog
            jobId={questId}
            skillName={skillName}
            canAct={isAdmin}
            onClose={() => set("quest", null)}
            onDone={() => set("quest", nextQuest)}
            onSkip={
              reviewAt >= 0 && nextQuest
                ? () => set("quest", nextQuest)
                : undefined
            }
            position={
              reviewAt >= 0
                ? `${reviewAt + 1} of ${reviewOrder.length}`
                : undefined
            }
          />
        )}
        {params.get("write") && (
          <WriteQuestDialog
            characters={data.characters}
            repos={repos}
            initialAgent={
              params.get("write") === "1" ? null : params.get("write")
            }
            onClose={() => set("write", null)}
            onSent={() => set("write", null)}
          />
        )}
      </div>
    </Tabs>
  );
}
