/**
 * The shape of `POST /api/plan` — the dry run.
 *
 * Mirrors `backend/coursegen/exam/plan.py::build_plan`. Costs no quota: the
 * solver is deterministic and makes no model calls, so this can be re-fetched as
 * freely as the UI likes.
 */

export interface PlanSection {
  section_id: string;
  title: string;
  item_type: string;
  marks_each: number;
  slots_requested: number;
  slots_planned: number;
  /** Slots the uploaded material could not support. 0 when the section is whole. */
  short_by: number;
  marks_planned: number;
  /** False for synthesis sections: answered by building something new. */
  from_material: boolean;
  topic: string | null;
  matched_nodes: number | null;
  /** The student's own files feeding this section. */
  sources: string[];
}

export interface PlanSummary {
  slots_total: number;
  slots_planned: number;
  fill_ratio: number;
  allocation_fidelity: number;
  marks_planned: number;
  short_by_marks: number;
  synthesis_items: number;
  sources_used: string[];
  bloom_realised: Record<string, number> | null;
  bloom_declared: Record<string, number> | null;
  warnings: string[];
  /** Honest cost preview, before the student commits their quota. */
  estimated_calls: number;
}

export interface Plan {
  blueprint_id: string;
  title: string;
  total_marks: number;
  duration_minutes: number;
  sections: PlanSection[];
  summary: PlanSummary;
}
