import { useEffect, useState } from "react";
import { getProgress } from "./api";
import type { LectureProgress, Progress } from "./types";
import "./practice.css";

/**
 * Where the student stands, lecture by lecture.
 *
 * Mastery is never shown alone: "90% mastery" over a twentieth of a lecture says
 * little, so every figure sits beside how much of the lecture has been tested.
 * An untested lecture says "not tested yet" — it is not weak, and the next paper
 * does not treat it as weak.
 */

interface Props {
  /** Injectable for tests; the real screen reads `/api/progress`. */
  load?: () => Promise<Progress>;
  onStartReview?: () => void;
  onNextPaper?: () => void;
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

export function ProgressScreen({ load = getProgress, onStartReview, onNextPaper }: Props) {
  const [data, setData] = useState<Progress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    load()
      .then((p) => live && setData(p))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Progress could not be read."));
    return () => {
      live = false;
    };
  }, [load]);

  return (
    <article className="paper progress" aria-label="Your progress">
      <header className="paper__head">
        <h1 className="paper__title">Your progress</h1>
        {data && (
          <p className="results__sub">
            {data.attempts} attempt{data.attempts === 1 ? "" : "s"} · {data.due} re-test
            {data.due === 1 ? "" : "s"} due
          </p>
        )}
      </header>

      {error && <p className="sheet__error" role="alert">{error}</p>}

      {data && data.warnings.length > 0 && (
        <div className="notice notice--empty" role="status">
          <ul className="notice__list">
            {data.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {data && (
        <>
          <div className="progress__acts">
            {onStartReview && (
              <button
                type="button"
                className="sheet__submit"
                disabled={data.next_review === 0}
                onClick={onStartReview}
              >
                Start review ({data.next_review})
              </button>
            )}
            {onNextPaper && (
              <button type="button" className="reveal" onClick={onNextPaper}>
                Next paper, aimed at my weak topics
              </button>
            )}
          </div>

          <ul className="progress__list">
            {data.lectures.map((l) => (
              <Lecture key={l.lecture} lecture={l} />
            ))}
          </ul>

          <p className="progress__note">
            Re-tests come back after a set number of attempts, not days: a question
            you miss returns at your next review, then 2, 4 and 8 attempts later.
          </p>
        </>
      )}
    </article>
  );
}

function Lecture({ lecture }: { lecture: LectureProgress }) {
  const tested = lecture.mastery !== null;
  return (
    <li className="progress__row" data-lecture={lecture.lecture}>
      <div className="progress__name">{lecture.lecture}</div>
      <div
        className="progress__bar"
        role="img"
        aria-label={tested ? `Mastery ${pct(lecture.mastery!)}` : "Not tested yet"}
      >
        {tested && (
          <div className="progress__fill" style={{ width: `${lecture.mastery! * 100}%` }} />
        )}
      </div>
      <div className="progress__figure">
        {tested
          ? `${pct(lecture.mastery!)} mastery · ${pct(lecture.tested_share)} of the material tested`
          : "Not tested yet"}
      </div>
      {lecture.nodes.length > 0 && (
        <details className="progress__nodes">
          <summary>
            {lecture.nodes_tested} of {lecture.nodes_total} sections tested
          </summary>
          <ul>
            {lecture.nodes.map((n) => (
              <li key={n.node_id}>
                <span>{n.topic}</span>
                <span className="progress__nodefig">{pct(n.mastery)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}
