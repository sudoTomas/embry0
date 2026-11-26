/**
 * The provider catalogue's overlay, as the Providers page's form edits it
 * (docs/providers.md, "Changing the catalogue"; ADR 0031).
 *
 * The overlay names only what differs from the catalogue embry0 ships: per
 * provider `enabled`, `factors`, `default_price`, `refresh_hours` and
 * `models`, where a model the overlay adds comes with its `prices` or
 * `free: true`, a shipped one takes the fields the overlay gives it, and
 * `null` takes a shipped one out. The form works on these values and writes
 * them as YAML (`overlayYaml`); the server checks every write whole, so what
 * this module does not know it does not decide: the save's review is the
 * server's dry run.
 */

/** A price: US dollars per million tokens; `from` the day it starts, none for the first. */
export interface CataloguePrice {
  in: number;
  out: number;
  from?: string | null;
}

/** A model as the catalogue in force has it (`GET /config/providers/catalog`'s `catalogue`). */
export interface CatalogueModel {
  prices: CataloguePrice[];
  free: boolean;
  chat_only: boolean;
  request_extras: Record<string, unknown>;
  structured_output?: string | null;
  /** A custom model's context window in tokens (#174); a whole number. */
  context_window?: number | null;
}

/** A provider as the catalogue in force has it. */
export interface CatalogueProvider {
  runtime: string;
  enabled: boolean;
  /** The prefixes its model ids start with. */
  model_prefixes?: string[];
  factors?: { turns: number; timeout: number };
  default_price?: { in: number; out: number } | null;
  refresh_hours?: number | null;
  models: Record<string, CatalogueModel>;
}

/** A model's part of the overlay: the fields it sets; `null` takes a shipped model out. */
export type OverlayModel = Partial<CatalogueModel>;

/** A provider's part of the overlay. */
export interface OverlayProvider {
  enabled?: boolean;
  factors?: { turns?: number; timeout?: number };
  default_price?: { in: number; out: number } | null;
  refresh_hours?: number;
  models?: Record<string, OverlayModel | null>;
}

/** The overlay's values (`GET /config/providers/catalog`'s `data`). */
export interface CatalogueOverlay {
  version?: number;
  providers?: Record<string, OverlayProvider>;
  [key: string]: unknown;
}

/** The overlay with nothing shared with `overlay`, so an edit never changes what it was made from. */
export function cloneOverlay(overlay: CatalogueOverlay | null | undefined): CatalogueOverlay {
  return JSON.parse(JSON.stringify(overlay ?? {})) as CatalogueOverlay;
}

function providerOf(overlay: CatalogueOverlay, name: string): OverlayProvider {
  overlay.providers ??= {};
  overlay.providers[name] ??= {};
  return overlay.providers[name];
}

/** Drop what an edit left empty, so the overlay names only what differs. */
function tidy(overlay: CatalogueOverlay, name: string): CatalogueOverlay {
  const provider = overlay.providers?.[name];
  if (provider) {
    if (provider.models && Object.keys(provider.models).length === 0) delete provider.models;
    if (Object.keys(provider).length === 0) delete overlay.providers![name];
  }
  if (overlay.providers && Object.keys(overlay.providers).length === 0) delete overlay.providers;
  return overlay;
}

/**
 * The overlay with `name` turned on or off. Turning it back to what embry0
 * ships drops the key, so the overlay keeps naming only what differs.
 */
export function withEnabled(
  overlay: CatalogueOverlay,
  name: string,
  enabled: boolean,
  shipped: boolean,
): CatalogueOverlay {
  const next = cloneOverlay(overlay);
  const provider = providerOf(next, name);
  if (enabled === shipped) delete provider.enabled;
  else provider.enabled = enabled;
  return tidy(next, name);
}

/** What the form edits of a model: its price (none when it is free), its flags. */
export interface ModelEdit {
  price: { in: number; out: number } | null;
  free: boolean;
  chatOnly: boolean;
}

/**
 * The overlay with `model` of `name` set as `edit` says: the deployment's
 * own model, or its changes to a shipped one. A free model has no price,
 * and a priced one is not free. Its request extras and structured output are
 * kept as they are (they are edited under Advanced).
 */
export function withModel(overlay: CatalogueOverlay, name: string, model: string, edit: ModelEdit): CatalogueOverlay {
  const next = cloneOverlay(overlay);
  const provider = providerOf(next, name);
  provider.models ??= {};
  const kept = provider.models[model] ?? {};
  const entry: OverlayModel = { ...kept };
  if (edit.free) {
    entry.free = true;
    delete entry.prices;
  } else {
    entry.prices = edit.price ? [{ in: edit.price.in, out: edit.price.out }] : [];
    delete entry.free;
  }
  if (edit.chatOnly) entry.chat_only = true;
  else delete entry.chat_only;
  provider.models[model] = entry;
  return tidy(next, name);
}

/** The overlay without its entry for `model`: a shipped model back to what embry0 ships, an added one gone. */
export function withoutModelEntry(overlay: CatalogueOverlay, name: string, model: string): CatalogueOverlay {
  const next = cloneOverlay(overlay);
  const models = next.providers?.[name]?.models;
  if (models) delete models[model];
  return tidy(next, name);
}

/** The overlay taking out `model`, a model embry0 ships (`null`). */
export function withModelTakenOut(overlay: CatalogueOverlay, name: string, model: string): CatalogueOverlay {
  const next = cloneOverlay(overlay);
  const provider = providerOf(next, name);
  provider.models ??= {};
  provider.models[model] = null;
  return next;
}

/** Where a model of the catalogue in force comes from. */
export type ModelSource = "shipped" | "changed" | "added" | "taken out";

/** Whether the overlay leaves `model` of `name` as embry0 ships it, changes it, adds it, or takes it out. */
export function modelSource(
  overlay: CatalogueOverlay,
  name: string,
  model: string,
  shipped: Record<string, unknown> | undefined,
): ModelSource {
  const models = overlay.providers?.[name]?.models;
  if (!models || !(model in models)) return "shipped";
  if (models[model] === null) return "taken out";
  return shipped && model in shipped ? "changed" : "added";
}

/** Whether a model's prices are more than one undated price, which the form cannot edit without losing some. */
export function hasDatedPrices(model: Pick<CatalogueModel, "prices"> | null | undefined): boolean {
  const prices = model?.prices ?? [];
  return prices.length > 1 || prices.some((price) => Boolean(price.from));
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const PLAIN_KEY = /^[A-Za-z_][A-Za-z0-9_./-]*$/;
/**
 * A plain key YAML 1.1 (what the catalogue's loader reads) would take for something else: a bool or null
 * (`on`, `No`, `TRUE`, `~`), a number or a date. Such a key is quoted.
 */
const NOT_A_STRING =
  /^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF|null|Null|NULL|~|[-+]?(?:\d[\d_]*)?\.?\d[\d_]*(?:[eE][-+]?\d+)?|\.(?:inf|Inf|INF|nan|NaN|NAN)|\d{4}-\d{1,2}-\d{1,2}.*)$/;

/**
 * A number as plain decimals, never in exponent form (`1e-7`, `1e+21`), which the catalogue's loader would read
 * as a string. From JavaScript's own shortest form of it, so no digit is made up.
 */
export function plainDecimal(value: number): string {
  const text = String(value);
  const match = /^(-?)(\d+)(?:\.(\d+))?e([-+]\d+)$/.exec(text);
  if (!match) return text;
  const [, sign, whole, fraction = "", power] = match;
  const digits = whole + fraction;
  const point = whole.length + Number(power);
  if (point <= 0) return `${sign}0.${"0".repeat(-point)}${digits}`;
  if (point >= digits.length) return `${sign}${digits}${"0".repeat(point - digits.length)}`;
  return `${sign}${digits.slice(0, point)}.${digits.slice(point)}`;
}

/**
 * The overlay's fields the schema types as whole numbers, written as integers: a float there is refused. The shared
 * fixture's `whole_number_keys` pins this list, and the backend checks that list against the overlay's schema.
 */
export const WHOLE_NUMBER_KEYS: ReadonlySet<string> = new Set(["version", "context_window"]);
/**
 * A model's fields of extra request body: JSON, which YAML reads as written, so their numbers stay as they are. The
 * shared fixture's `json_keys` pins this list, and the backend checks it against the schema's free-form fields.
 */
export const EXTRAS_KEYS: ReadonlySet<string> = new Set(["request_extras", "background_request_extras"]);
/**
 * The fields written as a bare YAML date, as the catalogue reads a day; any other string is quoted. The shared
 * fixture's `date_keys` pins this list, and the backend checks it against the schema's date fields.
 */
export const DATE_KEYS: ReadonlySet<string> = new Set(["from"]);

function scalar(value: unknown, key: string): string {
  // Every number of the overlay but a whole-number field's (a price, a factor, hours) is a float, so a whole one is
  // written with its ".0".
  if (typeof value === "number" && WHOLE_NUMBER_KEYS.has(key)) return String(value);
  if (typeof value === "number") {
    const text = plainDecimal(value);
    return text.includes(".") ? text : `${text}.0`;
  }
  // A price's day is a YAML date, as the catalogue reads it; any other string is quoted.
  if (DATE_KEYS.has(key) && typeof value === "string" && DAY.test(value)) return value;
  return JSON.stringify(value) ?? "null";
}

function keyText(key: string): string {
  return PLAIN_KEY.test(key) && !NOT_A_STRING.test(key) ? key : JSON.stringify(key);
}

/** A request extra's value: JSON, which YAML reads as written. Its numbers stay as they are. */
function flow(value: unknown): string {
  return JSON.stringify(value) ?? "null";
}

function block(value: unknown, indent: number, key: string, out: string[]): void {
  const pad = " ".repeat(indent);
  if (Array.isArray(value)) {
    if (value.length === 0) {
      out[out.length - 1] += " []";
      return;
    }
    for (const item of value) {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const entries = Object.entries(item as Record<string, unknown>).filter(([, v]) => v !== undefined && v !== null);
        out.push(`${pad}- {${entries.map(([k, v]) => `${keyText(k)}: ${scalar(v, k)}`).join(", ")}}`);
      } else {
        out.push(`${pad}- ${scalar(item, key)}`);
      }
    }
    return;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined);
    if (entries.length === 0) {
      out[out.length - 1] += " {}";
      return;
    }
    for (const [k, v] of entries) {
      if (EXTRAS_KEYS.has(k) && v && typeof v === "object") {
        out.push(`${pad}${keyText(k)}: ${flow(v)}`);
      } else if (v !== null && typeof v === "object") {
        out.push(`${pad}${keyText(k)}:`);
        block(v, indent + 2, k, out);
      } else {
        out.push(`${pad}${keyText(k)}: ${v === null ? "null" : scalar(v, k)}`);
      }
    }
  }
}

/**
 * The overlay as YAML the catalogue reads back to the same values: keys in
 * the order they hold, a model id quoted where YAML needs it, a price's day
 * as a date, a request extra as JSON. Written from the values, so a comment
 * the stored text held is not in it. An empty overlay is the empty text, the
 * shipped catalogue as it is.
 */
export function overlayYaml(overlay: CatalogueOverlay): string {
  const entries = Object.entries(overlay).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  const out: string[] = [];
  for (const [key, value] of entries) {
    if (value !== null && typeof value === "object") {
      out.push(`${keyText(key)}:`);
      block(value, 2, key, out);
    } else {
      out.push(`${keyText(key)}: ${value === null ? "null" : scalar(value, key)}`);
    }
  }
  return `${out.join("\n")}\n`;
}

/** Whether a stored overlay's text holds a comment, which a write from the form's values does not keep. */
export function hasComments(text: string): boolean {
  return /^\s*#/m.test(text) || / #/.test(text);
}

/** A model row of the form: the model as the edited overlay makes it, or null when it takes it out. */
export interface ModelRow {
  id: string;
  model: CatalogueModel | null;
  source: ModelSource;
}

/** A provider as the edited overlay makes it, for the form: whether it is on, and each model's row. */
export interface ProviderView {
  enabled: boolean;
  models: ModelRow[];
}

/**
 * `shipped`, a provider as embry0 ships it, with `overlay`'s part of it merged
 * in, as the catalogue merges it: a model the overlay gives fields takes
 * them (a free one drops its prices, a priced one is not free), a model it
 * adds is its entry, and one it takes out is a row with no model. Shipped
 * models first, in their order, then the overlay's own.
 */
export function providerView(shipped: CatalogueProvider, overlay: OverlayProvider | undefined): ProviderView {
  const given = overlay?.models ?? {};
  const rows: ModelRow[] = [];
  const merged = (base: CatalogueModel | undefined, entry: OverlayModel): CatalogueModel => {
    const model: CatalogueModel = {
      prices: [],
      free: false,
      chat_only: false,
      request_extras: {},
      ...(base ?? {}),
      ...entry,
    } as CatalogueModel;
    if (entry.free && !("prices" in entry)) model.prices = [];
    if (entry.prices && entry.prices.length > 0 && !("free" in entry)) model.free = false;
    return model;
  };
  for (const [id, base] of Object.entries(shipped.models ?? {})) {
    if (!(id in given)) rows.push({ id, model: base, source: "shipped" });
    else if (given[id] === null) rows.push({ id, model: null, source: "taken out" });
    else rows.push({ id, model: merged(base, given[id]!), source: "changed" });
  }
  for (const [id, entry] of Object.entries(given)) {
    if (id in (shipped.models ?? {}) || entry === null) continue;
    rows.push({ id, model: merged(undefined, entry), source: "added" });
  }
  return { enabled: overlay?.enabled ?? shipped.enabled, models: rows };
}

/** The price a model runs at today in the form's terms: its one undated price, or none. */
export function plainPrice(model: CatalogueModel | null): { in: number; out: number } | null {
  if (!model || model.free || hasDatedPrices(model)) return null;
  const [first] = model.prices;
  return first ? { in: first.in, out: first.out } : null;
}
