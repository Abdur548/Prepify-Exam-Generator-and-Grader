import { useState } from "react";
import type { AttemptResult, ItemResult } from "./types";
import "../paper/paper.css";
import "./practice.css";

/**
 * What the student got, and what to do about it.
 *
 * ## Copy rules — each one stops a specific false impression
 *
 * - The total is read against what has been MARKED. "31 / 60" over a paper with
 *   two answers still unmarked would be a number the student cannot trust.
 * - A model's mark is a judgement and says so, every time, with the way out:
 *   "contest any mark". Nothing here calls a mark verified or correct.
 * - The key's own answer is labelled "answer key", because a generated key can
 *   be wrong — which is also why a key mark can be contested.
 * - A synthesis question points at no page. Sending a student to a passage that
 *   never contained the answer is worse than sending them nowhere.
 */

interface Props {
  result: AttemptResult;
  onContest(slotId: string, marks: number): Promise<void>;
  /** Marks the written answers still waiting. Absent: no retry is offered. */
  onRemark?(): Promise<void>;
  onDone(): void;
}

export function Results({ result, onContest, onRemark, onDone }: Props) {
  const { totals } = result;
  const pending = totals.unmarked > 0;
  return (
    <article className="paper results" aria-label="Your marks">
      <header className="paper__head">
        <h1 className="paper__title">Your marks</h1>
        <p className="results__total">
          {pending
            ? `${totals.marks_awarded} / ${totals.marks_marked} marked so far`
            : `${totals.marks_awarded} / ${totals.marks_available}`}
        </p>
        {(totals.unmarked > 0 || totals.unanswered > 0) && (
          <p className="results__sub">
            {[
              totals.unmarked > 0 && `${totals.unmarked} not marked yet`,
              totals.unanswered > 0 && `${totals.unanswered} not answered`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
        {pending && onRemark && <RemarkButton onRemark={onRemark} />}
      </header>

      {(result.status === "degraded" || result.warnings.length > 0) && (
        <div className={`notice notice--${result.status === "degraded" ? "degraded" : "empty"}`} role="status">
          <ul className="notice__list">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {result.items.map((item, i) => (
        <ResultRow key={item.slot_id} item={item} number={i + 1} onContest={onContest} />
      ))}

      <footer className="sheet__foot">
        <button type="button" className="sheet__submit" onClick={onDone}>
          Done
        </button>
      </footer>
    </article>
  );
}

function RemarkButton({ onRemark }: { onRemark(): Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <p className="results__retry">
      <button
        type="button"
        className="reveal"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await onRemark();
          } catch (e) {
            setError(e instanceof Error ? e.message : "Marking did not run.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Marking…" : "Retry marking"}
      </button>
      {error && <span className="results__error" role="alert">{error}</span>}
    </p>
  );
}

function sourceLine(item: ItemResult): string {
  if (item.source === "key") return "Marked against the answer key.";
  if (item.source === "model") {
    return "Marked by the model against your material — contest any mark.";
  }
  if (item.source === "student") {
    const was = item.original ? ` (was ${item.original.marks_awarded})` : "";
    return item.objective_contest
      ? `Your mark — you disagree with the answer key${was}.`
      : `Your mark${was}.`;
  }
  return "";
}

function ResultRow({
  item,
  number,
  onContest,
}: {
  item: ItemResult;
  number: number;
  onContest(slotId: string, marks: number): Promise<void>;
}) {
  const fb = item.feedback;
  const [showPassage, setShowPassage] = useState(false);
  const picked = fb.options.find((o) => o.label === item.response.trim().toUpperCase());
  const keyed = fb.options.find((o) => o.label === fb.correct_option);

  return (
    <section className="item results__item" data-slot={item.slot_id}>
      <div className="item__body">
        <p className="item__stem">
          <span className="item__number">Q{number}.</span>
          {fb.stem}
        </p>

        <p className="results__answer">
          <span className="results__label">Your answer</span>
          {item.status === "unanswered"
            ? "—"
            : picked
              ? `(${picked.label.toLowerCase()}) ${picked.text}`
              : item.response}
        </p>

        <p className="results__source">
          {sourceLine(item)}
          {item.confidence === "low" && (
            <span className="results__badge">Check with your teacher</span>
          )}
        </p>
        {item.reason && <p className="results__reason">{item.reason}</p>}

        <div className="answer">
          <p className="answer__value">
            <span className="results__label">
              {fb.correct_option ? "Answer key" : "Model answer"}
            </span>
            {keyed ? `(${keyed.label.toLowerCase()}) ${keyed.text}` : fb.model_answer}
          </p>
          {fb.explanation && <p className="answer__why">{fb.explanation}</p>}
        </div>

        {fb.revise ? (
          <div className="results__revise">
            <button
              type="button"
              className="results__chip"
              aria-expanded={showPassage}
              onClick={() => setShowPassage((v) => !v)}
            >
              Revise: {fb.revise.file} · p.{fb.revise.pages.join(", ")}
            </button>
            {showPassage && fb.source_excerpt && (
              <blockquote className="src__text">{fb.source_excerpt}</blockquote>
            )}
          </div>
        ) : (
          <p className="cite--none">Not in your material — see the model answer.</p>
        )}

        {item.status !== "unanswered" && (
          <ContestControl item={item} onContest={onContest} />
        )}
      </div>

      <div className="item__marks results__mark" aria-label="Mark">
        {item.status === "marked"
          ? `${item.marks_awarded} / ${item.marks_available}`
          : item.status === "unmarked"
            ? `– / ${item.marks_available}`
            : "Not answered"}
      </div>
    </section>
  );
}

function ContestControl({
  item,
  onContest,
}: {
  item: ItemResult;
  onContest(slotId: string, marks: number): Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const id = `contest-${item.slot_id}`;
  const label = item.status === "unmarked" ? "Mark it yourself" : "Contest";

  const submit = async () => {
    const marks = Number(value);
    if (value.trim() === "" || !Number.isInteger(marks) || marks < 0 || marks > item.marks_available) {
      setError(`Enter a whole number from 0 to ${item.marks_available}.`);
      return;
    }
    setError("");
    setBusy(true);
    try {
      await onContest(item.slot_id, marks);
      setValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That mark was not saved.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="results__contest">
      <label htmlFor={id}>Your mark</label>
      <input
        id={id}
        type="number"
        min={0}
        max={item.marks_available}
        step={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="button" className="reveal" disabled={busy} onClick={submit}>
        {label}
      </button>
      {error && <span className="results__error" role="alert">{error}</span>}
    </div>
  );
}
