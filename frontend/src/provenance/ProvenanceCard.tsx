import { useCallback, useRef, useState } from "react";
import "./provenance.css";

/**
 * Drag a question aside to see the page it was written from.
 *
 * The signature gesture (`UI-DESIGN.md` §4). Papersetter's split answers *"how
 * will this be delivered?"*; this one answers *"why should I believe this
 * question?"* — the only thing a student actually wants to know about a paper an
 * AI wrote. Dragging right dissolves the question into its source; releasing
 * short of halfway springs it back, past halfway latches it open.
 *
 * ## The same component on the landing page and in the paper
 *
 * That is the point of building it standalone. If the marketing demo were its own
 * mock, the demo would eventually stop being true — so the gesture a visitor plays
 * with is the control a student uses, with different children passed in.
 *
 * ## Two deliberate departures from the sketch
 *
 * 1. **No highlighted "supporting line".** The sketch marks the one line inside
 *    the slide that backs the answer. Nothing in the system knows which line that
 *    is — `source_excerpt` is the passage the question was written from, not a
 *    located claim — so the trace marks the passage and stops there. Marking a
 *    line would be inventing evidence, which is the exact failure R7 exists for.
 * 2. **A button as well as a drag.** A drag-only affordance is unusable by
 *    keyboard and by anyone who cannot make the gesture. Both drive the same
 *    state, so nothing is reachable one way and not the other.
 */

interface Props {
  /** The exam side. Serif, and the thing that is on screen at rest. */
  question: React.ReactNode;
  /** The evidence side. Revealed by the drag. */
  source: React.ReactNode;
  /** Label for the toggle, e.g. "03_search.pdf · p.48". */
  sourceLabel: string;
  /**
   * False for a synthesis item: there is no page to reveal, so the gesture is
   * disabled rather than opening onto an apology.
   */
  enabled?: boolean;
}

/** Past this fraction of the card's width, a release latches instead of springing. */
const LATCH_AT = 0.5;

/**
 * How far a pointer must travel before it is a drag rather than a selection.
 *
 * Without this the card claimed every press: reading a question and dragging
 * across its stem to select the text moved the card instead, and there was no
 * way to select question text with a mouse at all.
 *
 * The gesture also has to be mostly horizontal. A press that moves down the page
 * is someone selecting several lines, or scrolling on a trackpad, and neither
 * should dissolve the question they are reading.
 */
const DRAG_THRESHOLD_PX = 8;

export function ProvenanceCard({
  question,
  source,
  sourceLabel,
  enabled = true,
}: Props) {
  const [open, setOpen] = useState(false);
  // Null except during a drag. Keeping "am I dragging" in the same value as "how
  // far" is what stops the transition from fighting the pointer: a number means
  // follow the finger with no easing, null means animate to the latched state.
  const [drag, setDrag] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const startX = useRef(0);
  const startY = useRef(0);
  // A press that has not yet decided whether it is a drag or a selection.
  const pending = useRef(false);

  const progress = drag ?? (open ? 1 : 0);

  const onDown = useCallback(
    (e: React.PointerEvent) => {
      if (!enabled) return;
      // Let text selection and the toggle button win; a card that hijacks every
      // press makes the question unreadable.
      if ((e.target as HTMLElement).closest("button, a")) return;
      startX.current = e.clientX;
      startY.current = e.clientY;
      // Armed, not started. `setDrag` waits until the pointer proves intent, so a
      // press that turns out to be a selection never disturbs the card.
      pending.current = true;
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        // Throws NotFoundError if the pointer is already gone — a fast tap, or a
        // synthetic event. Capture is an improvement (the drag keeps tracking
        // outside the card), not a requirement, and there is no error boundary
        // above this: letting it throw would take the whole paper down to lose a
        // gesture that works fine without it.
      }
    },
    [enabled, open],
  );

  const onMove = useCallback(
    (e: React.PointerEvent) => {
      if (drag === null && !pending.current) return;

      const dx = e.clientX - startX.current;
      const dy = e.clientY - startY.current;

      // A non-finite delta would sail through every check below, because every
      // comparison against NaN is false — the threshold would not stop it, the
      // axis test would not stop it, and `drag` would be set to NaN, which is
      // not null and so leaves the card permanently mid-gesture. Real browsers
      // always send coordinates; this costs one comparison to make the failure
      // impossible rather than merely unlikely.
      if (!Number.isFinite(dx) || !Number.isFinite(dy)) return;

      if (drag === null) {
        // Still deciding. Below the threshold, do nothing at all — the browser
        // keeps handling it as a selection.
        if (Math.abs(dx) < DRAG_THRESHOLD_PX && Math.abs(dy) < DRAG_THRESHOLD_PX) {
          return;
        }
        // Committed, but only if the intent was sideways. A mostly vertical
        // press is a selection down the page or a scroll, and it releases the
        // gesture rather than fighting it.
        pending.current = false;
        if (Math.abs(dy) > Math.abs(dx)) return;
      }

      const width = box.current?.offsetWidth ?? 1;
      const base = open ? 1 : 0;
      setDrag(Math.min(1, Math.max(0, base + dx / width)));
    },
    [drag, open],
  );

  const onUp = useCallback(() => {
    pending.current = false;
    // A press that never crossed the threshold was a click or a selection, and
    // must not latch the card open on release.
    if (drag === null) return;
    setOpen(drag > LATCH_AT);
    setDrag(null);
  }, [drag]);

  return (
    <div
      ref={box}
      className={`prov${open ? " prov--open" : ""}${drag !== null ? " prov--dragging" : ""}`}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <div
        className="prov__face prov__face--question"
        style={{
          transform: `translateX(${-progress * 38}%)`,
          opacity: 1 - progress,
        }}
        // Hidden from assistive tech once it is visually gone, so a screen reader
        // is not reading a question the sighted user has swapped away.
        aria-hidden={progress > 0.85}
      >
        {question}
      </div>

      <div
        className="prov__face prov__face--source"
        style={{
          transform: `translateX(${(1 - progress) * 22}%)`,
          opacity: progress,
          // `visibility` rather than `display`, so the height the question
          // reserves is never recomputed mid-drag and the card cannot jump.
          visibility: progress < 0.02 ? "hidden" : "visible",
        }}
        aria-hidden={progress < 0.85}
      >
        {source}
      </div>

      {enabled && (
        <button
          type="button"
          className="prov__toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Back to the question" : `Where this came from · ${sourceLabel}`}
        </button>
      )}
    </div>
  );
}
