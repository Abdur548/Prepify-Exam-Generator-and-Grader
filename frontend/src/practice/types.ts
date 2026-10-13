/**
 * The shapes of the practice API.
 *
 * Mirrors `backend/coursegen/grade/service.py::results` and the "Practice" section of
 * `docs/API-CONTRACT.md`. If either changes, this changes in the same commit.
 */
import type { Paper, PaperItem, PaperOption, PaperSource } from "../paper/types";

/** Mirrors `config.MAX_ANSWER_CHARS`. Longer is refused by the server, never truncated. */
export const MAX_ANSWER_CHARS = 4000;

/** A review item re-tests an earlier question; `form` says how. */
export type RetestForm = "identical" | "fresh" | "fallback";

export type SheetItem = PaperItem & {
  form?: RetestForm | null;
  retest_of?: { sheet_id: string; slot_id: string } | null;
};

/** Anything a student can answer: a generated paper, or a review set. */
export type SheetDoc = Omit<Paper, "sections"> & {
  kind?: "paper" | "review";
  sections: (Omit<Paper["sections"][number], "items"> & { items: SheetItem[] })[];
};

/** `unmarked` is not zero: nobody has marked it yet. `unanswered` was left blank. */
export type ItemStatus = "marked" | "unmarked" | "unanswered";

export type MarkSource = "key" | "model" | "student";

export interface Feedback {
  stem: string;
  options: PaperOption[];
  correct_option: string | null;
  model_answer: string;
  explanation: string;
  from_material: boolean;
  topic: string | null;
  /** Null for a synthesis question: there is no page in the student's material. */
  revise: PaperSource | null;
  source_excerpt: string | null;
}

export interface ItemResult {
  slot_id: string;
  item_type: string;
  marks_available: number;
  response: string;
  status: ItemStatus;
  marks_awarded: number | null;
  source: MarkSource | null;
  confidence: "high" | "low" | null;
  reason: string | null;
  /** The mark a contest replaced. Null unless the student's own mark counts. */
  original: { marks_awarded: number; source: string } | null;
  /** The student disagrees with the answer KEY, not with a model's judgement. */
  objective_contest: boolean;
  form: RetestForm | null;
  feedback: Feedback;
}

export interface AttemptTotals {
  marks_awarded: number;
  /** What has been marked. Read the total against this, never `marks_available`. */
  marks_marked: number;
  marks_available: number;
  unmarked: number;
  unanswered: number;
}

export interface AttemptResult {
  attempt_id: string;
  sheet_id: string;
  sheet_kind: "paper" | "review";
  status: "ok" | "degraded";
  totals: AttemptTotals;
  items: ItemResult[];
  warnings: string[];
}

export interface NodeProgress {
  node_id: string;
  topic: string;
  mastery: number;
  answers: number;
}

export interface LectureProgress {
  lecture: string;
  /** Null when no question from this lecture has been answered: untested, not zero. */
  mastery: number | null;
  /** How much of the lecture, by instructional mass, has been tested at all. */
  tested_share: number;
  nodes_total: number;
  nodes_tested: number;
  /** Tested nodes only. */
  nodes: NodeProgress[];
}

/** Mirrors `backend/coursegen/grade/service.py::progress`. */
export interface Progress {
  attempts: number;
  lectures: LectureProgress[];
  cards: Record<string, number>;
  /** Every re-test that is due. */
  due: number;
  /** How many the next review set will hold — capped, one per passage. */
  next_review: number;
  warnings: string[];
}

/** Mirrors `POST /api/review`. `sheet` is null when nothing is due. */
export interface ReviewResponse {
  due: number;
  sheet: SheetDoc | null;
  fallbacks: number;
  orphans: number;
  warnings: string[];
}

/** How each kind of re-test is labelled above its question. */
export const RETEST_TAGS: Record<RetestForm, string> = {
  identical: "re-test: same question",
  fresh: "re-test: new question",
  fallback: "couldn't write a new question — here's the original",
};
