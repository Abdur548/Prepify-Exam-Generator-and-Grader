/**
 * The shape of `POST /api/chat`.
 *
 * Mirrors `backend/coursegen/app/main.py::chat`. If that changes, this changes in
 * the same commit.
 */

export interface Citation {
  file: string;
  page: number;
}

export interface ChatAnswer {
  answer: string;
  citations: Citation[];
  /**
   * **The field this screen exists to render.**
   *
   * `false` means the answer was NOT drawn from the student's uploads. It is not
   * an error and not a lower-quality answer — it is a different *kind* of answer,
   * and the only one they cannot check against a page of their own notes. Shown
   * without trace colour and labelled, per `UI-DESIGN.md` §5.6.
   */
  from_material: boolean;
  /** Present only when a known limit was hit — quota, or the model unreachable. */
  status?: "degraded";
}

export interface Turn {
  id: number;
  question: string;
  /** Null while in flight. */
  answer: ChatAnswer | null;
  /** Set instead of `answer` when the request itself failed. */
  error?: string;
  /** True when the server refused because it was indexing or generating. */
  busy?: boolean;
}
