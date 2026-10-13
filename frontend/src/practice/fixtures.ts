import type { AttemptResult, ItemResult, SheetDoc } from "./types";

/** A two-section sheet: one MCQ, one short answer, one unfilled slot. */
export const SHEET: SheetDoc = {
  sheet_id: "3f9c0a1b2d4e5f60",
  title: "Search quiz",
  blueprint_id: "quiz_default",
  blueprint_title: "Quiz",
  total_marks: 6,
  duration_minutes: 20,
  instructions: [],
  summary: {
    items_total: 2, slots_total: 3, fill_ratio: 2 / 3, allocation_fidelity: 1,
    unfilled_slots: ["A-02"], marks_available: 6, synthesis_items: 0, warnings: [],
  },
  sections: [
    {
      section_id: "A", title: "Multiple choice", item_type: "mcq", marks_each: 2,
      count: 2, instruction: "1 question, 2 marks each.",
      items: [
        {
          slot_id: "A-01", filled: true, marks: 2, item_type: "mcq",
          stem: "Which data structure does breadth-first search use?",
          options: [{ label: "A", text: "Stack" }, { label: "B", text: "Queue" }],
          correct_option: "B", model_answer: "B. Queue", explanation: "FIFO order.",
          from_material: true, source: { file: "03_search.pdf", pages: [30] },
          source_excerpt: "BFS expands nodes in FIFO order.",
        },
        { slot_id: "A-02", filled: false, marks: 2, item_type: "mcq" },
      ],
    },
    {
      section_id: "B", title: "Short answer", item_type: "short", marks_each: 4,
      count: 1, instruction: "1 question, 4 marks each.",
      items: [
        {
          slot_id: "B-01", filled: true, marks: 4, item_type: "short",
          stem: "Explain why BFS is complete.", model_answer: "It explores every depth in turn.",
          explanation: "Finite branching.", from_material: true,
          source: { file: "03_search.pdf", pages: [31] }, source_excerpt: "…",
        },
      ],
    },
  ],
};

function item(over: Partial<ItemResult>): ItemResult {
  return {
    slot_id: "A-01", item_type: "mcq", marks_available: 2, response: "A",
    status: "marked", marks_awarded: 0, source: "key", confidence: null, reason: null,
    original: null, objective_contest: false, form: null,
    feedback: {
      stem: "Which data structure does breadth-first search use?",
      options: [{ label: "A", text: "Stack" }, { label: "B", text: "Queue" }],
      correct_option: "B", model_answer: "B. Queue", explanation: "FIFO order.",
      from_material: true, topic: "Lecture 3 › Search",
      revise: { file: "03_search.pdf", pages: [30] },
      source_excerpt: "BFS expands nodes in FIFO order.",
    },
    ...over,
  };
}

export const RESULT: AttemptResult = {
  attempt_id: "att1", sheet_id: SHEET.sheet_id!, sheet_kind: "paper", status: "ok",
  totals: { marks_awarded: 31, marks_marked: 44, marks_available: 60, unmarked: 1, unanswered: 1 },
  warnings: [],
  items: [
    item({}),
    item({
      slot_id: "B-01", item_type: "short", marks_available: 4, response: "It goes level by level.",
      marks_awarded: 2, source: "model", confidence: "low", reason: "Names the order, not why it terminates.",
      feedback: { ...item({}).feedback, options: [], correct_option: null,
                  model_answer: "It explores every depth in turn.", stem: "Explain why BFS is complete." },
    }),
    item({
      slot_id: "B-02", item_type: "short", marks_available: 4, response: "Because of the frontier.",
      status: "unmarked", marks_awarded: null, source: null,
      feedback: { ...item({}).feedback, options: [], correct_option: null, model_answer: "…", stem: "Why?" },
    }),
    item({
      slot_id: "C-01", item_type: "long", marks_available: 10, response: "",
      status: "unanswered", marks_awarded: null, source: null,
      feedback: { ...item({}).feedback, options: [], correct_option: null, from_material: false,
                  revise: null, source_excerpt: null, model_answer: "A game tree.", stem: "Build a game tree." },
    }),
  ],
};
