import { useState } from "react";
import { loadDraft, saveDraft } from "./drafts";
import { MAX_ANSWER_CHARS, type SheetDoc, type SheetItem } from "./types";
import "../paper/paper.css";
import "./practice.css";

/**
 * A sheet, answerable. Papers and review sets share this component, so they
 * share one way of answering and one way of being marked.
 *
 * Set in the paper's own classes: taking a paper should look like the paper,
 * not like a form that happens to contain it.
 */

interface Props {
  sheet: SheetDoc;
  submitting: boolean;
  error?: string;
  onSubmit(responses: Record<string, string>): void;
  /** A short label above a question, e.g. how a review item re-tests it. */
  tagFor?(item: SheetItem): string | null;
}

export function AnswerSheet({ sheet, submitting, error, onSubmit, tagFor }: Props) {
  const sheetId = sheet.sheet_id ?? "";
  const [answers, setAnswers] = useState<Record<string, string>>(() => loadDraft(sheetId));

  const set = (slot: string, value: string) =>
    setAnswers((prev) => {
      const next = { ...prev, [slot]: value };
      saveDraft(sheetId, next);
      return next;
    });

  const answerable = sheet.sections.flatMap((s) => s.items.filter((i) => i.filled));
  const answered = answerable.filter((i) => (answers[i.slot_id] ?? "").trim()).length;

  return (
    <form
      className="paper sheet"
      aria-label={`Answer ${sheet.title}`}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(answers);
      }}
    >
      <header className="paper__head">
        <h1 className="paper__title">{sheet.title}</h1>
        <p className="sheet__lead">
          Answer what you can. Multiple-choice questions are marked against the
          answer key; written answers are marked by the model, and you can contest
          any mark.
        </p>
      </header>

      {sheet.sections.map((section) => {
        const items = section.items.filter((i) => i.filled);
        if (items.length === 0) return null;
        return (
          <section key={section.section_id} className="section">
            <h2 className="section__head">
              <span className="section__label">Section {section.section_id}</span>
              <span className="section__title">{section.title}</span>
            </h2>
            {items.map((item, i) => (
              <Question
                key={item.slot_id}
                item={item}
                number={i + 1}
                value={answers[item.slot_id] ?? ""}
                tag={tagFor?.(item) ?? null}
                onChange={(v) => set(item.slot_id, v)}
              />
            ))}
          </section>
        );
      })}

      <footer className="sheet__foot">
        <span className="sheet__count">
          {answered} of {answerable.length} answered
        </span>
        <button type="submit" className="sheet__submit" disabled={submitting}>
          {submitting ? "Marking…" : "Submit answers"}
        </button>
      </footer>
      {error && (
        <p className="sheet__error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function Question({
  item,
  number,
  value,
  tag,
  onChange,
}: {
  item: SheetItem;
  number: number;
  value: string;
  tag: string | null;
  onChange(value: string): void;
}) {
  const objective = Boolean(item.options && item.options.length > 0);
  return (
    <div className="item" data-slot={item.slot_id}>
      <div className="item__body">
        {tag && <p className="sheet__tag">{tag}</p>}
        <p className="item__stem">
          <span className="item__number">Q{number}.</span>
          {item.stem}
        </p>
        {objective ? (
          <fieldset className="sheet__choices">
            <legend className="visually-hidden">Options for question {number}</legend>
            {item.options!.map((o) => (
              <label key={o.label} className="sheet__choice">
                <input
                  type="radio"
                  name={item.slot_id}
                  value={o.label}
                  checked={value === o.label}
                  onChange={() => onChange(o.label)}
                />
                <span className="options__label">({o.label.toLowerCase()})</span>
                <span className="options__text">{o.text}</span>
              </label>
            ))}
          </fieldset>
        ) : (
          <textarea
            className="sheet__written"
            aria-label={`Your answer to question ${number}`}
            rows={item.item_type === "long" ? 10 : 4}
            maxLength={MAX_ANSWER_CHARS}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
      </div>
      <div className="item__marks" aria-label={`${item.marks} marks`}>
        [{item.marks}]
      </div>
    </div>
  );
}
