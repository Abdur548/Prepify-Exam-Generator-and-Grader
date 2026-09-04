import { useEffect, useState } from "react";
import type { Plan } from "../plan/types";
import type { Paper as PaperDoc } from "../paper/types";
import type { RunOutcome, StageId } from "./types";
import { useGenerationRun } from "./useGenerationRun";
import "../paper/paper.css";
import "./generate.css";

/**
 * The wait, told honestly.
 *
 * A first generation costs about fifty seconds, of which roughly forty are the
 * model writing questions. A spinner over that reads as a hang, so this screen
 * shows the pipeline's real stages, streamed from the run itself — not a timer
 * pretending to be one. Every number on it arrived in an event.
 *
 * ## Why the skeleton is not decoration
 *
 * The section headings, the slot counts and the marks down the rail all come from
 * the dry run the student already read on the blueprint screen. They are the same
 * numbers, in the same geometry as the finished paper — this file imports
 * `paper.css` rather than restating its measurements, so the two cannot drift.
 * The paper is on screen from the first second; only the sentences are missing.
 *
 * ## What it deliberately does not show
 *
 * Questions arriving one at a time. Nothing is final until every batch is back,
 * because the duplication gate compares items against each other — an item shown
 * as written at ten seconds can still be rejected at forty. So the placeholders
 * pulse together, never in sequence, and nothing here implies per-question
 * progress the pipeline cannot actually report.
 */

interface Props {
  blueprintId: string;
  plan: Plan;
  onDone: (paper: PaperDoc, outcome: RunOutcome) => void;
  onBack: () => void;
  /** The server answered 403: the disclosure gate has not been met, or reset. */
  onNeedsDisclosure?: () => void;
}

const STAGES: { id: StageId; label: string }[] = [
  { id: "reading", label: "Reading your material" },
  { id: "choosing", label: "Choosing what to ask" },
  { id: "writing", label: "Writing questions" },
  // NOT "Checking sources", which UI-DESIGN.md originally specified. Nothing in
  // this stage establishes that a question is true, and that label would tell a
  // student it did. The gates that do run check relevance and duplication, and
  // they run inside "Writing questions".
  { id: "assembling", label: "Putting the paper together" },
];

export function GeneratingScreen(props: Props) {
  const [attempt, setAttempt] = useState(0);
  // A fresh instance per attempt: the run guards itself against starting twice,
  // so a retry needs a new one rather than a re-render of the spent one.
  return <Run key={attempt} {...props} onRetry={() => setAttempt((a) => a + 1)} />;
}

function Run({
  blueprintId,
  plan,
  onDone,
  onBack,
  onRetry,
  onNeedsDisclosure,
}: Props & { onRetry: () => void }) {
  const run = useGenerationRun(blueprintId, plan.title);
  const [handoffError, setHandoffError] = useState("");

  // The run reports the outcome; the paper itself lives at GET /api/paper. Two
  // calls rather than one fat response, because generation takes the better part
  // of a minute and a refresh mid-wait must not lose the paper.
  useEffect(() => {
    if (run.phase !== "done" || !run.result) return;
    let live = true;
    fetch("/api/paper")
      .then(async (r) => {
        if (!r.ok) throw new Error(`Could not load the finished paper (HTTP ${r.status}).`);
        return (await r.json()) as PaperDoc;
      })
      .then((paper) => live && onDone(paper, run.result!))
      .catch((e) => live && setHandoffError(String(e.message || e)));
    return () => {
      live = false;
    };
  }, [run.phase, run.result, onDone]);

  const failed = run.phase === "error" || run.phase === "needs_disclosure" || handoffError;

  return (
    <div className="gen">
      <header className="gen__head">
        <div className="gen__headmain">
          <h1 className="gen__title">
            {failed ? "Generation stopped" : "Writing your paper"}
          </h1>
          <p className="gen__sub">
            {plan.title} · {plan.summary.slots_planned} questions ·{" "}
            {plan.summary.marks_planned} marks
          </p>
        </div>
        {!failed && <Elapsed seconds={run.elapsed} />}
      </header>

      {failed ? (
        <Failure
          phase={run.phase}
          message={handoffError || run.error}
          hint={handoffError ? "" : run.errorHint}
          onRetry={onRetry}
          onBack={onBack}
          onNeedsDisclosure={onNeedsDisclosure}
        />
      ) : (
        <>
          {run.waiting && (
            <p className="gen__prelude">
              Another paper is being generated right now. Yours starts as soon as
              that one finishes.
            </p>
          )}
          {run.loading && (
            <p className="gen__prelude">
              Warming up the language models. This happens once per session and is
              most of why a first paper is slow.
            </p>
          )}

          <StageRail run={run} />
          <Skeleton plan={plan} writing={run.active === "writing"} />
        </>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- stages -- */

function StageRail({ run }: { run: ReturnType<typeof useGenerationRun> }) {
  const detail = describe(run);
  return (
    <div className="rail" aria-live="polite">
      <ol className="rail__steps">
        {STAGES.map((s) => {
          const done = run.completed.includes(s.id);
          const active = run.active === s.id;
          return (
            <li
              key={s.id}
              className={`step${done ? " step--done" : ""}${active ? " step--active" : ""}`}
            >
              <span className="step__dot" aria-hidden="true" />
              <span className="step__label">{s.label}</span>
            </li>
          );
        })}
      </ol>
      <p className="rail__detail">{detail}</p>
    </div>
  );
}

/** Every sentence here is built from an event. Nothing is inferred from a clock. */
function describe(run: ReturnType<typeof useGenerationRun>): string {
  const { batch } = run;
  // Measured on a cold run: the model load holds here for ~24 of the ~27 seconds
  // before the first question is sent. The prelude above already says what is
  // happening, and the old fallback claimed "Opening your course material" for
  // that whole stretch — a sentence about work that had not started.
  if ((run.loading || run.waiting) && run.completed.length === 0) return "";
  if (run.active === "writing" && batch) {
    return batch.phase === "rewriting"
      ? `Rewriting ${batch.total} question${batch.total === 1 ? "" : "s"} the checks sent back — ${batch.done} done.`
      : `${batch.done} of ${batch.total} questions written.`;
  }
  if (run.active === "writing") {
    return "Sending your material to the model, a few questions at a time.";
  }
  if (run.active === "assembling") {
    return "Laying out the paper and the answer key.";
  }
  if (run.active === "choosing" || run.completed.includes("choosing")) {
    if (run.slots !== null && run.sections !== null) {
      return `${run.slots} question${run.slots === 1 ? "" : "s"} placed across ${run.sections} section${run.sections === 1 ? "" : "s"}.`;
    }
    return "Working out which topics carry the paper.";
  }
  if (run.topics !== null) {
    return `${run.topics} topics found in your uploads.`;
  }
  return "Opening your course material.";
}

function Elapsed({ seconds }: { seconds: number }) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return (
    <div className="gen__clock">
      <span className="gen__time">{`${m}:${String(s).padStart(2, "0")}`}</span>
      {/* Measured, not guessed: P6-EVALUATION §E3.1. */}
      <span className="gen__expect">first paper takes about a minute</span>
    </div>
  );
}

/* -------------------------------------------------------------- skeleton -- */

function Skeleton({ plan, writing }: { plan: Plan; writing: boolean }) {
  return (
    <div className={`paper gen__skeleton${writing ? " gen__skeleton--busy" : ""}`} aria-hidden="true">
      <h2 className="paper__title">{plan.title}</h2>
      <div className="paper__meta">
        <span>
          <b>{plan.summary.marks_planned}</b> marks
        </span>
        <span>
          <b>{plan.duration_minutes}</b> minutes
        </span>
      </div>

      {plan.sections.map((section) => (
        <section key={section.section_id} className="section">
          <h3 className="section__head">
            <span className="section__label">Section {section.section_id}</span>
            {section.title}
          </h3>
          {/* slots_planned, not slots_requested: the dry run already told the
              student this section would come up short, and drawing rows the
              material cannot fill would quietly take that back. */}
          {Array.from({ length: section.slots_planned }, (_, i) => (
            <div key={i} className="item">
              <div className="ghost">
                <span className="ghost__line ghost__line--a" />
                <span className="ghost__line ghost__line--b" />
              </div>
              <div className="item__marks">[{section.marks_each}]</div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- failure -- */

function Failure({
  phase,
  message,
  hint,
  onRetry,
  onBack,
  onNeedsDisclosure,
}: {
  phase: string;
  message: string;
  hint: string;
  onRetry: () => void;
  onBack: () => void;
  onNeedsDisclosure?: () => void;
}) {
  const disclosure = phase === "needs_disclosure";
  return (
    <div className="gen__failure">
      <p className="gen__failmsg">
        {disclosure
          ? "The server needs you to accept the disclosure before it will generate a paper."
          : message}
      </p>
      {/* The hint travels with the failure rather than being fixed copy: a run
          that never started and a connection that dropped mid-run call for
          opposite advice, and the wrong one sends the student to pay twice. */}
      <p className="gen__failhint">
        {disclosure
          ? "This resets whenever the server restarts, so it is a normal thing to meet."
          : hint ||
            "The paper was not handed over. Check the Paper tab before generating again."}
      </p>
      <div className="gen__failacts">
        {disclosure ? (
          // A way through, not just an explanation. This used to be a dead end:
          // the screen named the gate correctly and offered nothing but "back".
          onNeedsDisclosure && (
            <button
              type="button"
              className="gen__btn gen__btn--go"
              onClick={onNeedsDisclosure}
            >
              Read it now
            </button>
          )
        ) : (
          <button type="button" className="gen__btn gen__btn--go" onClick={onRetry}>
            Try again
          </button>
        )}
        <button type="button" className="gen__btn" onClick={onBack}>
          Back to the blueprint
        </button>
      </div>
    </div>
  );
}
