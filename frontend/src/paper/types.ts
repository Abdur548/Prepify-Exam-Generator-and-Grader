/**
 * The shape of `GET /api/paper`.
 *
 * Mirrors `backend/coursegen/exam/paper.py::build_paper`. If that changes, this
 * changes in the same commit — a contract that drifts from its producer is worse
 * than no contract, because the client trusts it.
 */

export interface PaperSource {
  file: string;
  pages: number[];
}

/** `null` means gate 5 did not run. It does not mean "checked and unsupported". */
export type Factuality = "SUPPORTED" | "CONTRADICTED" | "NOT_STATED" | null;

export interface PaperOption {
  label: string;
  text: string;
}

/**
 * A slot in the paper. `filled: false` is a real state, not an error — the solver
 * ran out of material for it. The renderer shows the gap rather than closing up,
 * so a short section is legible as a short section.
 */
export interface PaperItem {
  slot_id: string;
  filled: boolean;
  marks: number;
  item_type: string;

  stem?: string;
  options?: PaperOption[];
  correct_option?: string | null;
  model_answer?: string;
  explanation?: string;
  bloom?: string;

  /** False for synthesis items: written to be answered by building something new. */
  from_material?: boolean;
  source?: PaperSource | null;
  /** The passage the question was written from. Absent for synthesis items. */
  source_excerpt?: string | null;
  factuality?: Factuality;
}

export interface PaperSection {
  section_id: string;
  title: string;
  item_type: string;
  marks_each: number;
  count: number;
  instruction: string;
  items: PaperItem[];
}

export interface PaperSummary {
  items_total: number;
  slots_total: number;
  fill_ratio: number;
  allocation_fidelity: number;
  unfilled_slots: string[];
  /** What the paper is actually worth, which is not always `total_marks`. */
  marks_available: number;
  synthesis_items: number;
  warnings: string[];
}

export interface Paper {
  title: string;
  blueprint_id: string;
  blueprint_title: string;
  total_marks: number;
  duration_minutes: number;
  instructions: string[];
  sections: PaperSection[];
  summary: PaperSummary;
}
