import { useState } from "react";
import { ProvenanceCard } from "../provenance/ProvenanceCard";
import { PaperActions } from "./PaperActions";
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
      {/* Above the title, in the grotesque: taking the paper away is a thing you
          do WITH the exam, not part of it. Nothing inside `.paper` is chrome. */}
      <PaperActions />

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
        {/* §4: the question and the page it came from occupy one space, and the
            drag moves between them. Both faces are always mounted, so the paper's
            line rhythm never shifts under the gesture. */}
        <ProvenanceCard
          enabled={Boolean(sourced)}
          sourceLabel={
            sourced ? `${item.source!.file} · p.${item.source!.pages.join(", ")}` : ""
          }
          question={
            <>
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
            </>
          }
          source={<SourceFace item={item} />}
        />

        <div className="item__tools">
          {!sourced && (
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


/**
 * The evidence side of the drag: the passage the question was written from.
 *
 * The wording is load-bearing. "Written from" is what the manifest actually
 * records; "supported by" or "verified against" would claim a check nothing in
 * this system performs (R6). The factuality line below is the only place a
 * stronger statement appears, and only when gate 5 actually ran — `undefined`
 * means it did not, which is different from checking and finding nothing.
 */
function SourceFace({ item }: { item: PaperItem }) {
  if (!(item.from_material && item.source)) {
    return (
      <aside className="src src--synthesis" aria-label="Not from your material">
        <p className="src__ref">no source</p>
        <p className="src__text">
          Not from your material — this question asks you to build something new.
        </p>
      </aside>
    );
  }

  return (
    <aside className="src" aria-label="Source passage">
      <p className="src__ref">
        {item.source.file} · page {item.source.pages.join(", ")}
      </p>
      <blockquote className="src__text">{item.source_excerpt}</blockquote>
      <p className="src__note">
        {item.factuality === "SUPPORTED"
          ? "The cited page states this."
          : item.factuality === null || item.factuality === undefined
            ? "Written from this page. Not checked against it."
            : "The cited page does not state this answer."}
      </p>
    </aside>
  );
}
