import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { DefaultMovedNotice } from "@/components/game-content/DefaultMovedNotice";
import { PackEditor } from "@/components/game-content/PackEditor";
import { PackHistory } from "@/components/game-content/PackHistory";
import { PackProblems } from "@/components/game-content/PackProblems";
import { useProgression } from "@/hooks/useProgression";
import { usePack } from "@/hooks/useProgressionPack";
import { problemLines } from "@/lib/progressionPack";

/**
 * The progression content pack (#176): every skill, rate, tier, rare,
 * achievement, quest, diary task and chat-box word the game uses, as one
 * YAML document. An admin edits a draft, previews what it re-prices and
 * activates it; the history keeps every version for undo. Everyone else
 * reads it.
 */
export default function GameContentTab() {
  const pack = usePack();
  const fallback = pack.data?.fallback;
  // The calendar the achievements were last counted on: UTC when the database does not know the pack's timezone.
  const counted = useProgression().data?.pack;
  const calendarRefused = !!counted?.timezone_applied && counted.timezone_applied !== counted.timezone;
  return (
    <div className="space-y-6">
      <p className="text-sm text-fg-secondary">
        The game's content: its skills and what trains them, the experience rates, the tiers, rares, achievements,
        quests and diary tasks, and the words the chat box uses. Until an admin activates a pack of their own, the
        deployment follows the default embry0 ships. A change re-prices the whole history, so save a draft and preview
        it before you activate it.
      </p>
      {fallback && (
        <PackProblems
          problems={problemLines(fallback)}
          testId="pack-fallback"
          title="The stored pack is refused by this release, so the shipped default is in effect. Fix these and activate it again:"
        />
      )}
      {calendarRefused && counted && (
        <div
          role="alert"
          data-testid="pack-timezone-refused"
          className="rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning [overflow-wrap:anywhere]"
        >
          {`The database does not know the pack's timezone, ${counted.timezone}, so the achievements that count a day or an hour are counted on ${counted.timezone_applied} for now. Name a timezone both know, such as Europe/Berlin, and activate the pack again.`}
        </div>
      )}
      {pack.data?.default_moved && <DefaultMovedNotice />}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Content pack</CardTitle>
        </CardHeader>
        <CardContent>
          <PackEditor />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">History</CardTitle>
        </CardHeader>
        <CardContent>
          <PackHistory />
        </CardContent>
      </Card>
    </div>
  );
}
