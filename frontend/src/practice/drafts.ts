/**
 * Unsent answers, kept per sheet so a refresh does not lose them.
 *
 * `sessionStorage`, not a server: a draft is a convenience for this tab, not a
 * record. Every access is guarded — private windows and blocked site data throw
 * on read and write — and failing to remember must never stop a student answering.
 */

const key = (sheetId: string) => `prepify.draft.${sheetId}`;

export function loadDraft(sheetId: string): Record<string, string> {
  try {
    const raw = sessionStorage.getItem(key(sheetId));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([, v]) => typeof v === "string"),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

export function saveDraft(sheetId: string, answers: Record<string, string>): void {
  try {
    sessionStorage.setItem(key(sheetId), JSON.stringify(answers));
  } catch {
    /* not remembering is acceptable; failing to answer is not */
  }
}

export function clearDraft(sheetId: string): void {
  try {
    sessionStorage.removeItem(key(sheetId));
  } catch {
    /* see saveDraft */
  }
}
