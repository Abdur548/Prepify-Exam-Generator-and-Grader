import type { AttemptResult, ItemResult, SheetDoc } from "./types";

/** A non-2xx answer. `status` lets a caller route a 403 to the consent screen. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * The server's `detail`, when it is a sentence written to be shown. A request
 * that fails validation answers with a list of field errors instead, which is
 * for developers, not students.
 */
function detailOf(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  return status >= 500
    ? "Something went wrong on the server. Your answers are not lost — try again."
    : "The request was not accepted.";
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* an empty or non-JSON body; the status still says what happened */
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(body, res.status));
  return body as T;
}

const json = (data: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export function submitAttempt(
  sheetId: string,
  responses: Record<string, string>,
): Promise<AttemptResult> {
  return call("/api/attempts", json({
    sheet_id: sheetId,
    answers: Object.entries(responses).map(([slot_id, response]) => ({ slot_id, response })),
  }));
}

export function getAttempt(attemptId: string): Promise<AttemptResult> {
  return call(`/api/attempts/${encodeURIComponent(attemptId)}`);
}

export function contestMark(
  attemptId: string,
  slotId: string,
  marks: number,
): Promise<ItemResult> {
  return call(
    `/api/attempts/${encodeURIComponent(attemptId)}/contest`,
    json({ slot_id: slotId, marks_awarded: marks }),
  );
}

export function getSheet(sheetId: string): Promise<SheetDoc> {
  return call(`/api/sheets/${encodeURIComponent(sheetId)}`);
}
