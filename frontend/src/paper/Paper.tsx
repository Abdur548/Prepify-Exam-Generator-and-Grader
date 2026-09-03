import { useState } from "react";
import type { Paper as PaperDoc, PaperItem, PaperSection } from "./types";
import "./paper.css";

/**
 * The exam paper.
 *
 * Three surfaces share one grid: the question column (serif — the exam), the
 * marks rail (mono — the evidence), and the app chrome around it (grotesque —
 * the tool). Which face a thing is set in tells you which world it belongs to.
 *
 * The marks rail is real blueprint data, not ornament. It is what separates an
 * exam paper from a broadsheet: a paper has a margin for marks and room to work.
 */

interface Props {
  paper: PaperDoc;
  /** Hide questions not drawn from the student's own material. */
  fromMaterialOnly?: boolean;
}

export function Paper({ paper, fromMaterialOnly = false }: Props) {
  const { summary } = paper;
  const short = summary.marks_available < paper.total_marks;

  // Counted off the paper, NOT off `summary.unfilled_slots`.
  //
  // Those are two different reasons a slot is empty. `unfilled_slots` is the
  // solver's: it could not place a question there at all. `filled: false` also
  // covers the slot that WAS placed, was written, and then lost its item at a
  // validation gate — allocation succeeded, so the slot never appears in
  // `unfilled_slots` and `fill_ratio` still reads 1.0.
  //
  // Conflating them printed "0 questions could not be built from your material"
  // directly above a gap it had just drawn, on a real run where one MCQ was
  // rejected twice for an option-length outlier. Seen 2026-09-03; no fixture had
  // ever produced a paper whose slots were allocated but not delivered.
  const gaps = paper.sections.reduce(
    (n, s) => n + s.items.filter((i) => !i.filled).length,
    0,
  );

  return (
    <article className="paper" aria-label={paper.title}>
      <PaperHead paper={paper} />

      {short && (
        <p className="paper__shortfall" role="status">
          This paper carries <strong>{summary.marks_available}</strong> of{" "}
          {paper.total_marks} marks. {gaps} question{gaps === 1 ? "" : "s"} could
          not be built from your material — {gaps === 1 ? "it is" : "they are"}{" "}
          marked below.
        </p>
      )}

      {paper.sections.map((section) => (
        <Section
          key={section.section_id}
          section={section}
          fromMaterialOnly={fromMaterialOnly}
        />
      ))}
    </article>
  );
}

function PaperHead({ paper }: { paper: PaperDoc }) {
  return (
    <header className="paper__head">
      <h1 className="paper__title">{paper.title}</h1>
      <p className="paper__subtitle">{paper.blueprint_title}</p>

      <div className="paper__meta">
        <span>
          Time allowed <b>{paper.duration_minutes} min</b>
        </span>
        <span>
          Maximum marks <b>{paper.total_marks}</b>
        </span>
      </div>

      <section className="paper__instructions" aria-label="General instructions">
        <h2>General instructions</h2>
        <ol>
          {paper.instructions.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ol>
      </section>
    </header>
  );
}

function Section({
  section,
  fromMaterialOnly,
}: {
  section: PaperSection;
  fromMaterialOnly: boolean;
}) {
  const items = fromMaterialOnly
    ? section.items.filter((i) => !i.filled || i.from_material)
    : section.items;

  // A section emptied entirely by the filter still announces itself. Silently
  // vanishing would read as "this section does not exist" rather than "everything
  // here came from somewhere else".
  return (
    <section className="section" aria-labelledby={`sec-${section.section_id}`}>
      <h2 className="section__head" id={`sec-${section.section_id}`}>
        <span className="section__label">Section {section.section_id}</span>
        <span className="section__title">{section.title}</span>
      </h2>
      <p className="section__instruction">{section.instruction}</p>

      {items.length === 0 ? (
        <p className="section__filtered">
          Every question in this section asks you to build something new, so none
          are shown while the filter is on.
        </p>
      ) : (
        items.map((item, i) => (
          <Item key={item.slot_id} item={item} number={i + 1} />
        ))
      )}
    </section>
  );
}

function Item({ item, number }: { item: PaperItem; number: number }) {
  const [showSource, setShowSource] = useState(false);
  const [showAnswer, setShowAnswer] = useState(false);

  if (!item.filled) {
    return (
      <div className="item item--gap">
        <div className="item__body">
          <p className="item__gaptext">
            No question here. Your material did not cover enough of this topic to
            build a {item.marks}-mark {item.item_type} question.
          </p>
        </div>
        <div className="item__marks" aria-label={`${item.marks} marks, unfilled`}>
          [{item.marks}]
        </div>
      </div>
    );
  }

  const sourced = item.from_material && item.source;

  return (
    <div className="item">
      <div className="item__body">
        <p className="item__stem">
          <span className="item__number">Q{number}.</span>
          {item.stem}
        </p>

        {item.options && item.options.length > 0 && (
          <ol className="options">
            {item.options.map((o) => (
              <li key={o.label} className="options__row">
                <span className="options__label">({o.label.toLowerCase()})</span>
                <span className="options__text">{o.text}</span>
              </li>
            ))}
          </ol>
        )}

        <div className="item__tools">
          {sourced ? (
            <button
              type="button"
              className="cite"
              aria-expanded={showSource}
              onClick={() => setShowSource((v) => !v)}
            >
              <span className="cite__mark" aria-hidden="true" />
              <span className="cite__ref">
                {item.source!.file} · p.{item.source!.pages.join(", ")}
              </span>
            </button>
          ) : (
            /* No trace colour. The plainness IS the signal — a badge here would
               read as a warning, and a synthesis question is not a fault. */
            <span className="cite cite--none">
              Not in your uploads — build your own answer
            </span>
          )}

          <button
            type="button"
            className="reveal"
            aria-expanded={showAnswer}
            onClick={() => setShowAnswer((v) => !v)}
          >
            {showAnswer ? "Hide answer" : "Show answer"}
          </button>
        </div>

        {showSource && sourced && (
          <aside className="source" aria-label="Source passage">
            <p className="source__ref">
              {item.source!.file} · page {item.source!.pages.join(", ")}
            </p>
            <blockquote className="source__text">{item.source_excerpt}</blockquote>
            <p className="source__note">
              {item.factuality === "SUPPORTED"
                ? "This passage states the answer."
                : item.factuality === null || item.factuality === undefined
                  ? "Not checked against the passage."
                  : "The passage does not state this answer."}
            </p>
          </aside>
        )}

        {showAnswer && (
          <aside className="answer" aria-label="Answer">
            <p className="answer__value">{item.model_answer}</p>
            {item.explanation && (
              <p className="answer__why">{item.explanation}</p>
            )}
          </aside>
        )}
      </div>

      <div className="item__marks" aria-label={`${item.marks} marks`}>
        [{item.marks}]
      </div>
    </div>
  );
}
