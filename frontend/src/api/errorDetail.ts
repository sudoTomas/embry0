/**
 * Readable text from a FastAPI error `detail`. The field is a string when a
 * route raises `HTTPException(detail="...")`, and a list of pydantic error
 * objects (`{type, loc, msg, input}`) when request validation fails. Only
 * `loc` and `msg` are read: `input` is the rejected value and may be a
 * secret, so it is never copied into a message.
 */

/** Request parts FastAPI puts first in `loc`; the field path follows. */
const REQUEST_PARTS = new Set(["body", "query", "path", "header", "cookie"]);

function itemMessage(item: unknown, withField: boolean): string {
  if (typeof item === "string") return item.trim();
  if (!item || typeof item !== "object") return "";
  const rec = item as { loc?: unknown; msg?: unknown; message?: unknown };
  const raw = typeof rec.msg === "string" ? rec.msg : typeof rec.message === "string" ? rec.message : "";
  const msg = raw.trim();
  if (!msg || !withField || !Array.isArray(rec.loc)) return msg;
  const path = rec.loc.filter((part, index) => !(index === 0 && REQUEST_PARTS.has(String(part))));
  return path.length > 0 ? `${path.map(String).join(".")}: ${msg}` : msg;
}

function messages(detail: unknown, withField: boolean): string[] {
  const items = Array.isArray(detail) ? detail : [detail];
  return items.map((item) => itemMessage(item, withField)).filter(Boolean);
}

/** The trimmed messages of a detail, without field paths. */
export function detailMessages(detail: unknown): string[] {
  return messages(detail, false);
}

/**
 * One line for a detail: each pydantic error as `field: msg`, joined by
 * `; `. Null when the detail holds nothing readable.
 */
export function formatErrorDetail(detail: unknown): string | null {
  const parts = messages(detail, true);
  return parts.length > 0 ? parts.join("; ") : null;
}

// Set on an error the api client's response interceptor already showed the
// operator: a toast for a network failure, a 422 or a 5xx, or the sign-in
// prompt for a 401.
const REPORTED = Symbol.for("embry0.errorReported");

export function markReported(error: unknown): void {
  if (error && typeof error === "object") (error as Record<symbol, unknown>)[REPORTED] = true;
}

/** Whether the api client already showed this error, so a caller must not toast it again. */
export function isReported(error: unknown): boolean {
  return !!error && typeof error === "object" && (error as Record<symbol, unknown>)[REPORTED] === true;
}

/**
 * The reason a request failed, for the operator: the API's own `detail`
 * (FastAPI's string, or each validation error as `field: msg`), never axios's
 * "Request failed with status code 409". Without a detail, a refused request
 * reads as `fallback (HTTP <status>)` and a thrown Error as its message.
 */
export function apiErrorMessage(error: unknown, fallback = "The request failed"): string {
  const e = error as {
    code?: unknown;
    message?: unknown;
    response?: { status?: unknown; data?: { detail?: unknown } };
  } | null;
  const detail = formatErrorDetail(e?.response?.data?.detail);
  if (detail) return detail;
  if (e?.code === "ERR_NETWORK") return "Network error — cannot reach the server";
  if (typeof e?.response?.status === "number") return `${fallback} (HTTP ${e.response.status})`;
  if (typeof e?.message === "string" && e.message.trim()) return e.message.trim();
  return fallback;
}

/** Whether a request failed because the record does not exist (HTTP 404). */
export function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: unknown } } | null)?.response?.status === 404;
}
