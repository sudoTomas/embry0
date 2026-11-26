import { Fragment, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { apiErrorMessage, isConflict } from "@/api/errorDetail";
import type { ProviderCatalogueDocument } from "@/api/providerCatalogue";
import { TextDiff } from "@/components/documents/TextDiff";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Switch } from "@/components/ui/Switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { usePreviewProviderCatalogue, useProviderCatalogue, useSaveProviderCatalogue } from "@/hooks/useProviderCatalogue";
import {
  cloneOverlay,
  hasComments,
  hasDatedPrices,
  overlayYaml,
  plainPrice,
  providerView,
  withEnabled,
  withModel,
  withModelTakenOut,
  withoutModelEntry,
  type CatalogueOverlay,
  type ModelRow,
} from "@/lib/catalogueOverlay";
import { documentProblems, type DocumentProblem } from "@/lib/settingsDocument";
import { providerLabel } from "@/lib/status";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { cn } from "@/lib/utils";

/** The source of a model row, in words. */
const SOURCE_LABEL: Record<ModelRow["source"], string> = {
  shipped: "Shipped",
  changed: "Changed",
  added: "Yours",
  "taken out": "Taken out",
};

/** A pending review: the text a save would store and what it would change. */
interface Review {
  text: string;
  diff: string;
  changed: boolean;
  from: "form" | "yaml";
}

function price(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/** A model row: a table row in a card wide enough for the table, else its cells wrap under its id. */
const STACKED_ROW = "flex flex-wrap items-center gap-x-3 gap-y-1 py-1 @lg:table-row @lg:py-0";
const STACKED_CELL = "block @lg:table-cell @lg:py-1 @lg:pr-2";
/** A switch's cell: its name and the switch on one line when stacked. */
const SWITCH_CELL = "flex items-center gap-1 @lg:table-cell @lg:py-1 @lg:pr-2";

/** A switch's name beside it in a stacked row, where the table's header is not shown; its own label names it to a
 * screen reader. */
function SwitchName({ children }: { children: ReactNode }) {
  return (
    <span aria-hidden className="text-xs text-fg-muted @lg:hidden">
      {children}
    </span>
  );
}

/** A model id that may break after a "/", "-" or ".", never inside a word (a word longer than the line still wraps). */
function ModelId({ id }: { id: string }) {
  const segments = id.match(/[^/.-]*[/.-]|[^/.-]+$/g) ?? [id];
  return (
    <>
      {segments.map((segment, index) => (
        <Fragment key={index}>
          {segment}
          {index < segments.length - 1 && <wbr />}
        </Fragment>
      ))}
    </>
  );
}

function ModelRowEditor({
  provider,
  row,
  disabled,
  overlay,
  onChange,
  onReplace,
}: {
  provider: string;
  row: ModelRow;
  disabled: boolean;
  overlay: CatalogueOverlay;
  /** A typed edit: the row keeps what is being typed. */
  onChange: (next: CatalogueOverlay) => void;
  /** An edit that replaces the row (reset, take out, put back, remove): the row starts again from the model. */
  onReplace: (next: CatalogueOverlay) => void;
}) {
  const model = row.model;
  const plain = plainPrice(model);
  const dated = model !== null && hasDatedPrices(model);
  const [inText, setInText] = useState(plain ? String(plain.in) : "");
  const [outText, setOutText] = useState(plain ? String(plain.out) : "");
  const testId = `catalogue-model-${provider}-${row.id}`;

  function edit(next: { inText?: string; outText?: string; free?: boolean; chatOnly?: boolean }) {
    if (!model) return;
    const free = next.free ?? model.free;
    const priceIn = price(next.inText ?? inText);
    const priceOut = price(next.outText ?? outText);
    onChange(
      withModel(overlay, provider, row.id, {
        free,
        chatOnly: next.chatOnly ?? model.chat_only,
        price: free || priceIn === null || priceOut === null ? null : { in: priceIn, out: priceOut },
      }),
    );
  }

  // The marker and the row's action sit under the id, in the first column. In a card narrower than the table
  // (a phone) the row stacks: the id on a line of its own, then the prices and the switches, each switch named
  // beside it, so nothing needs a sideways scroll.
  const action =
    disabled ? null : row.source === "taken out" ? (
      <Button size="xs" variant="ghost" onClick={() => onReplace(withoutModelEntry(overlay, provider, row.id))}>
        Put back
      </Button>
    ) : row.source === "changed" ? (
      <Button size="xs" variant="ghost" onClick={() => onReplace(withoutModelEntry(overlay, provider, row.id))}>
        Reset to shipped
      </Button>
    ) : row.source === "shipped" ? (
      <Button size="xs" variant="ghost" onClick={() => onReplace(withModelTakenOut(overlay, provider, row.id))}>
        Take out
      </Button>
    ) : (
      <Button size="xs" variant="ghost" onClick={() => onReplace(withoutModelEntry(overlay, provider, row.id))}>
        Remove
      </Button>
    );
  const idCell = (
    <td className={cn(STACKED_CELL, "w-full align-top @lg:w-auto")}>
      <div className={cn("break-words font-mono text-xs", model === null && "line-through")}>
        <ModelId id={row.id} />
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-1">
        <Badge tone={row.source === "shipped" ? "neutral" : row.source === "taken out" ? "warning" : "info"}>
          {SOURCE_LABEL[row.source]}
        </Badge>
        {action}
      </div>
    </td>
  );

  if (model === null) {
    return (
      <tr data-testid={testId} className={cn(STACKED_ROW, "text-fg-muted")}>
        {idCell}
        <td colSpan={4} className={cn(STACKED_CELL, "align-top text-xs")}>
          Taken out of the catalogue
        </td>
      </tr>
    );
  }
  return (
    <tr data-testid={testId} className={STACKED_ROW}>
      {idCell}
      <td className={STACKED_CELL}>
        {dated ? (
          <span className="text-xs text-fg-muted" title="Edit dated prices under Advanced">
            Dated prices
          </span>
        ) : (
          <Input
            aria-label={`${row.id} input price`}
            inputMode="decimal"
            className="h-7 w-20 text-xs"
            value={model.free ? "" : inText}
            placeholder={model.free ? "free" : "$/M in"}
            disabled={disabled || model.free}
            onChange={(e) => {
              setInText(e.target.value);
              edit({ inText: e.target.value });
            }}
          />
        )}
      </td>
      <td className={STACKED_CELL}>
        {dated ? null : (
          <Input
            aria-label={`${row.id} output price`}
            inputMode="decimal"
            className="h-7 w-20 text-xs"
            value={model.free ? "" : outText}
            placeholder={model.free ? "free" : "$/M out"}
            disabled={disabled || model.free}
            onChange={(e) => {
              setOutText(e.target.value);
              edit({ outText: e.target.value });
            }}
          />
        )}
      </td>
      <td className={SWITCH_CELL}>
        <SwitchName>Free</SwitchName>
        <Switch
          label={`${row.id} free`}
          checked={model.free}
          disabled={disabled || dated}
          onCheckedChange={(free) => edit({ free })}
        />
      </td>
      <td className={SWITCH_CELL}>
        <SwitchName>Chat-only</SwitchName>
        <Switch
          label={`${row.id} chat-only`}
          checked={model.chat_only}
          disabled={disabled || dated}
          onCheckedChange={(chatOnly) => edit({ chatOnly })}
        />
      </td>
    </tr>
  );
}

function AddModel({
  provider,
  prefixes,
  overlay,
  onChange,
}: {
  provider: string;
  prefixes: string[];
  overlay: CatalogueOverlay;
  onChange: (next: CatalogueOverlay) => void;
}) {
  const [id, setId] = useState("");
  const [free, setFree] = useState(false);
  const [inText, setInText] = useState("");
  const [outText, setOutText] = useState("");
  const priceIn = price(inText);
  const priceOut = price(outText);
  const ready = id.trim() !== "" && (free || (priceIn !== null && priceOut !== null));
  return (
    <div className="flex flex-wrap items-end gap-2" data-testid={`catalogue-add-${provider}`}>
      <div>
        <Label htmlFor={`catalogue-add-${provider}-id`} className="text-xs">
          Add a model
        </Label>
        <Input
          id={`catalogue-add-${provider}-id`}
          className="h-7 w-56 font-mono text-xs"
          placeholder={prefixes[0] ? `${prefixes[0]}…` : "model id"}
          value={id}
          onChange={(e) => setId(e.target.value)}
        />
      </div>
      <Input
        aria-label="New model input price"
        inputMode="decimal"
        className="h-7 w-20 text-xs"
        placeholder="$/M in"
        value={inText}
        disabled={free}
        onChange={(e) => setInText(e.target.value)}
      />
      <Input
        aria-label="New model output price"
        inputMode="decimal"
        className="h-7 w-20 text-xs"
        placeholder="$/M out"
        value={outText}
        disabled={free}
        onChange={(e) => setOutText(e.target.value)}
      />
      <label className="flex items-center gap-1 text-xs">
        <Switch label="New model free" checked={free} onCheckedChange={setFree} />
        Free
      </label>
      <Button
        size="xs"
        variant="outline"
        disabled={!ready}
        onClick={() => {
          onChange(
            withModel(overlay, provider, id.trim(), {
              free,
              chatOnly: false,
              price: free ? null : { in: priceIn!, out: priceOut! },
            }),
          );
          setId("");
          setInText("");
          setOutText("");
          setFree(false);
        }}
      >
        Add
      </Button>
    </div>
  );
}

function Problems({ problems }: { problems: DocumentProblem[] }) {
  return (
    <div
      role="alert"
      data-testid="catalogue-problems"
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <p className="font-semibold">The catalogue refused this change:</p>
      <ul className="list-disc pl-5">
        {problems.map((problem, index) => (
          <li key={index}>
            {problem.key && <code className="font-mono text-xs">{problem.key}</code>} {problem.msg}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The provider catalogue (#173, ADR 0031): every provider embry0 ships, its
 * models and prices, as this deployment's overlay makes them. An admin
 * changes them in a form per provider, or the overlay's YAML under
 * Advanced; either way the save is first reviewed, the server's dry run
 * showing what it would change, and is refused whole with every problem
 * named. A save is in force at once, with no restart.
 */
export function CatalogueEditor({ isAdmin }: { isAdmin: boolean }) {
  // A fault of this card is the card's: the rest of the Providers page (its list, its probes) stays.
  return (
    <ErrorBoundary fallback={<CardNote testId="catalogue-failed">The provider catalogue could not be shown.</CardNote>}>
      <CatalogueCard isAdmin={isAdmin} />
    </ErrorBoundary>
  );
}

function CardNote({ children, testId }: { children: ReactNode; testId: string }) {
  return (
    <Card data-testid="provider-catalogue">
      <CardHeader>
        <CardTitle className="text-lg">Provider catalogue</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-fg-secondary" data-testid={testId}>
          {children}
        </p>
      </CardContent>
    </Card>
  );
}

function CatalogueCard({ isAdmin }: { isAdmin: boolean }) {
  const doc = useProviderCatalogue();
  if (doc.isLoading) return <Skeleton className="h-40 w-full" />;
  if (doc.isError || !doc.data) {
    return <CardNote testId="catalogue-unread">The provider catalogue could not be read.</CardNote>;
  }
  if (!doc.data.shipped?.providers) {
    // An orchestrator of an earlier release (the dashboard deployed first, or the orchestrator rolled back) does
    // not say what embry0 ships, which the form edits against: it is not guessed from the catalogue in force.
    return (
      <CardNote testId="catalogue-needs-orchestrator">
        Editing the catalogue here needs this release&apos;s orchestrator. Until it runs, change the catalogue with{" "}
        <code className="font-mono text-xs">PUT /api/v1/config/providers/catalog</code>.
      </CardNote>
    );
  }
  return <Editor key={doc.data.generation} doc={doc.data} isAdmin={isAdmin} />;
}

function Editor({ doc, isAdmin }: { doc: ProviderCatalogueDocument; isAdmin: boolean }) {
  const preview = usePreviewProviderCatalogue();
  const save = useSaveProviderCatalogue();
  const [overlay, setOverlay] = useState<CatalogueOverlay>(() => cloneOverlay(doc.data));
  const [text, setText] = useState(doc.yaml_text);
  const [tab, setTab] = useState("form");
  const [review, setReview] = useState<Review | null>(null);
  const [problems, setProblems] = useState<DocumentProblem[] | null>(null);
  const [conflict, setConflict] = useState(false);
  // Bumped by an edit that replaces rows (a reset, a take-out, a discard): each row starts again from its model,
  // while one being typed into keeps its text.
  const [epoch, setEpoch] = useState(0);
  const formText = useMemo(() => overlayYaml(overlay), [overlay]);
  const formChanged = JSON.stringify(overlay) !== JSON.stringify(doc.data ?? {});
  const textChanged = text !== doc.yaml_text;
  const disabled = !isAdmin;

  function edited(next: CatalogueOverlay) {
    setOverlay(next);
    setReview(null);
    setProblems(null);
  }

  function replaced(next: CatalogueOverlay) {
    edited(next);
    setEpoch((value) => value + 1);
  }

  function failed(error: unknown) {
    if (isConflict(error)) {
      setConflict(true);
      return;
    }
    const found = documentProblems(error);
    if (found) setProblems(found);
    else toast.error(apiErrorMessage(error, "The catalogue could not be checked"));
  }

  function reviewText(candidate: string, from: Review["from"]) {
    setProblems(null);
    preview.mutate(
      { text: candidate, generation: doc.generation },
      {
        onSuccess: (result) => setReview({ text: candidate, diff: result.diff, changed: result.changed, from }),
        onError: failed,
      },
    );
  }

  function saveReviewed() {
    if (!review) return;
    save.mutate(
      { text: review.text, generation: doc.generation },
      {
        onSuccess: (result) => {
          toast.success(result.changed ? "Provider catalogue saved" : "Nothing changed");
          setReview(null);
        },
        onError: failed,
      },
    );
  }

  const reviewPanel = review && (
    <div className="space-y-2" data-testid="catalogue-review">
      <p className="text-sm font-medium">{review.changed ? "Saving changes the catalogue overlay:" : "Nothing would change."}</p>
      {review.from === "form" && hasComments(doc.yaml_text) && (
        <p className="text-xs text-warning" data-testid="catalogue-comments-dropped">
          The overlay is written again from its values, so its comments are not kept. Edit it under Advanced to keep
          them.
        </p>
      )}
      {review.changed && <TextDiff lines={parseUnifiedDiff(review.diff)} label="What saving changes" />}
      <div className="flex gap-2">
        <Button size="sm" disabled={!review.changed || save.isPending} onClick={saveReviewed}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setReview(null)}>
          Keep editing
        </Button>
      </div>
    </div>
  );

  return (
    <Card data-testid="provider-catalogue">
      <CardHeader>
        <CardTitle className="text-lg">Provider catalogue</CardTitle>
        <p className="text-sm text-fg-secondary">
          The providers and models agents can run on, and their prices in US dollars per million tokens. A change is
          in force at once, with no restart.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {(doc.refused ?? []).length > 0 && (
          <p className="text-sm text-warning" data-testid="catalogue-refused">
            Part of the stored overlay is not in force: {doc.refused.map((p) => p.loc.join(".")).join(", ")}.
          </p>
        )}
        {conflict && (
          <p role="alert" className="text-sm text-destructive" data-testid="catalogue-conflict">
            The catalogue changed since you opened it. Your edits are kept here; reload the page to start from the
            new version.
          </p>
        )}
        <Tabs value={tab} onValueChange={setTab} defaultValue="form">
          <TabsList>
            <TabsTrigger value="form">Providers</TabsTrigger>
            <TabsTrigger value="yaml">Advanced</TabsTrigger>
          </TabsList>
          <TabsContent value="form" className="space-y-6 pt-4">
            {Object.entries(doc.shipped.providers).map(([name, shipped]) => {
              const view = providerView(shipped, overlay.providers?.[name]);
              const alwaysOn = shipped.runtime === "sdk";
              return (
                <section key={name} className="space-y-2" data-testid={`catalogue-provider-${name}`}>
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold">{providerLabel(name)}</h3>
                    <Switch
                      label={`${providerLabel(name)} enabled`}
                      checked={view.enabled}
                      disabled={disabled || alwaysOn}
                      onCheckedChange={(on) => edited(withEnabled(overlay, name, on, shipped.enabled))}
                    />
                    {alwaysOn && <span className="text-xs text-fg-muted">Always on</span>}
                  </div>
                  {view.models.length > 0 && (
                    <div className="@container overflow-x-auto">
                      <table className="block w-full text-sm @lg:table">
                        <thead className="hidden text-left text-xs text-fg-muted @lg:table-header-group">
                          <tr>
                            <th className="pr-2 font-normal">Model</th>
                            <th className="pr-2 font-normal">In</th>
                            <th className="pr-2 font-normal">Out</th>
                            <th className="pr-2 font-normal">Free</th>
                            <th className="pr-2 font-normal">Chat-only</th>
                          </tr>
                        </thead>
                        <tbody className="block @lg:table-row-group">
                          {view.models.map((row) => (
                            <ModelRowEditor
                              key={`${row.id}-${epoch}`}
                              provider={name}
                              row={row}
                              disabled={disabled}
                              overlay={overlay}
                              onChange={edited}
                              onReplace={replaced}
                            />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {!disabled && (
                    <AddModel provider={name} prefixes={shipped.model_prefixes ?? []} overlay={overlay} onChange={edited} />
                  )}
                </section>
              );
            })}
            {isAdmin && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  disabled={!formChanged || preview.isPending}
                  onClick={() => reviewText(formText, "form")}
                  data-testid="catalogue-review-form"
                >
                  Review changes
                </Button>
                {formChanged && (
                  <Button size="sm" variant="ghost" onClick={() => replaced(cloneOverlay(doc.data))}>
                    Discard
                  </Button>
                )}
              </div>
            )}
          </TabsContent>
          <TabsContent value="yaml" className="space-y-2 pt-4">
            <Label htmlFor="catalogue-yaml">The overlay, as YAML</Label>
            <p className="text-xs text-fg-muted">
              Only what differs from the shipped catalogue: per provider <code>enabled</code>, <code>factors</code>,{" "}
              <code>default_price</code>, <code>models</code> and, for OpenRouter, <code>refresh_hours</code>.
              Comments are kept. An empty text is the catalogue embry0 ships.
            </p>
            <Textarea
              id="catalogue-yaml"
              className="min-h-64 font-mono text-xs"
              value={text}
              readOnly={disabled}
              onChange={(e) => {
                setText(e.target.value);
                setReview(null);
                setProblems(null);
              }}
            />
            {isAdmin && (
              <Button
                size="sm"
                disabled={!textChanged || preview.isPending}
                onClick={() => reviewText(text, "yaml")}
                data-testid="catalogue-review-yaml"
              >
                Review changes
              </Button>
            )}
          </TabsContent>
        </Tabs>
        {problems && <Problems problems={problems} />}
        {reviewPanel}
      </CardContent>
    </Card>
  );
}
