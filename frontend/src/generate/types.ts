/**
 * The shape of `POST /api/exam/stream` — NDJSON, one object per line.
 *
 * Mirrors `backend/coursegen/app/main.py::generate_exam_streamed` and the events
 * `coursegen/pipeline.py` fires. If those change, this changes in the same commit.
 *
 * ## The asymmetry worth knowing about
 *
 * `/api/exam` answers a failure with HTTP 500. This endpoint cannot: by the time
 * anything goes wrong its status line is long gone, so failure arrives as an
 * `error` event on a 200 response. A client must treat that event exactly as it
 * treats a 500 — same message, same retry, same absence of a paper.
 */

/**
 * `waiting` and `loading` are preconditions, not pipeline work — one is queueing
 * behind another generation, the other is the model load that dominates a cold
 * first request. The screen shows them above the stage rail rather than in it, so
 * the four real stages stay in one fixed place.
 */
export type StageId =
  | "waiting"
  | "loading"
  | "reading"
  | "choosing"
  | "writing"
  | "assembling";

export interface StageEvent {
  event: "stage";
  stage: StageId;
  state: "start" | "done";
  /** reading/done */
  topics?: number;
  /** choosing/done */
  slots?: number;
  slots_total?: number;
  sections?: number;
  /** writing/start */
  total?: number;
  /** writing/done */
  items?: number;
  calls?: number | null;
}

/**
 * Fires as each LLM batch returns. `rewriting` is a second pass over items the
 * gates rejected, counted against its own total — folding it into `writing` would
 * push the count past the number of questions in the paper.
 */
export interface BatchEvent {
  event: "batch";
  phase: "writing" | "rewriting";
  done: number;
  total: number;
}

/** The terminal success event. Carries exactly the `/api/exam` body. */
export interface ResultEvent {
  event: "result";
  status: "ok" | "empty" | "degraded";
  fill_ratio: number;
  allocation_fidelity: number;
  unfilled_slots: string[];
  warnings: string[];
  /** Present on ok/empty. A degraded body carries `items: []` instead. */
  items_count?: number;
  /** Absent on degraded — there is nothing complete to download. */
  downloads?: Record<string, string>;
}

export interface ErrorEvent {
  event: "error";
  detail: string;
}

export type RunEvent = StageEvent | BatchEvent | ResultEvent | ErrorEvent;

export type RunOutcome = ResultEvent;
