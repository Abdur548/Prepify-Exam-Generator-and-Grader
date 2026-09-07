import { useState } from "react";
import "./system.css";

/**
 * The consent gate (S7). `/api/exam` returns 403 until this is accepted.
 *
 * ## Why this copy is not the static UI's copy
 *
 * The original banner reads *"must be reviewed by a qualified educator before use
 * in any graded assessment… does not replace instructor judgment"*. That is
 * written for a teacher setting work, and the UI spec decided the primary
 * user is **the student revising their own notes**. Told they need an educator to
 * review their own revision quiz, a student learns nothing and skips the banner.
 *
 * So the lead is the thing that actually affects how they should read a paper:
 * **nothing here checks whether a question is true.** That is measured, not
 * precautionary — gate 2 was disproven on 2026-09-01 and the reranker cannot
 * separate true claims from false ones (`P6-EVALUATION.md` §E8). The educator
 * sentence stays, once, at the end, for the case where someone is setting work.
 *
 * ## What it must never say
 *
 * *Verified, accurate, grounded in, fact-checked, validated against* (R6). The
 * whole point of this screen is that none of those are true, so a reassuring word
 * here would do more damage than anywhere else in the product.
 */

interface Props {
  /** Called after the server has recorded acceptance. */
  onAccepted: () => void;
  onBack: () => void;
}

export function DisclosureScreen({ onAccepted, onBack }: Props) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const accept = async () => {
    setSending(true);
    setError("");
    try {
      const res = await fetch("/api/disclosure/accept", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onAccepted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="disc">
      <h1 className="disc__title">Before your first paper</h1>
      <p className="disc__lead">
        Prepify writes questions with a language model, from the slides you
        uploaded. Three things are true of every paper it makes.
      </p>

      <ol className="disc__points">
        <li>
          <strong>Nothing here checks whether a question is right.</strong>{" "}
          Prepify checks that a question is well-formed and on-topic. It does not
          check whether the answer is true, and no part of it can. That is a
          measured limit, not a disclaimer — so treat a paper as practice, never
          as a source.
        </li>
        <li>
          <strong>A citation says where a question came from.</strong> &ldquo;
          03_search.pdf&nbsp;p.48&rdquo; means the question was written from that
          page — not that the page proves the answer. Drag a question aside to
          read the page and judge for yourself.
        </li>
        <li>
          <strong>Some questions are not from your notes at all.</strong>{" "}
          Synthesis questions ask you to build something new. They are marked, and
          they carry no source, so there is nothing of yours to check them
          against.
        </li>
      </ol>

      <p className="disc__coda">
        If you are setting work that someone else will be graded on, a qualified
        educator needs to review it first.
      </p>

      {error && <p className="disc__error">Could not record that: {error}</p>}

      <div className="disc__acts">
        <button
          type="button"
          className="disc__btn disc__btn--go"
          onClick={() => void accept()}
          disabled={sending}
        >
          {sending ? "One moment…" : "I understand — continue"}
        </button>
        <button type="button" className="disc__btn" onClick={onBack}>
          Not yet
        </button>
      </div>

      <p className="disc__note">
        {/* The gate lives in process memory and resets when the server restarts,
            so a student will meet this again. Saying so stops it reading as a
            bug the second time. */}
        You&rsquo;ll see this again if the server restarts.
      </p>
    </div>
  );
}
