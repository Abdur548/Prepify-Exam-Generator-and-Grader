import { useCallback, useEffect, useRef, useState } from "react";
import type { IngestState } from "./types";

/**
 * Watch the server's ingest job, and start one.
 *
 * ## It polls before it does anything else
 *
 * The first thing this does on mount is ask the server what is happening — not
 * because something might have been missed, but because that is the feature. A
 * ten-minute wait will be reloaded, backgrounded and returned to, and the run
 * that is already in flight belongs to whoever opens the page next. There is no
 * client-side session to lose because nothing important is held here.
 *
 * ## No cancel, for a different reason than the generating screen
 *
 * There, a cancel button would have stopped the narration and not the run. Here
 * the server genuinely could support one — but a half-indexed corpus is worse
 * than either a finished one or none, since `course_map.json` and the Qdrant
 * collection would disagree. Until ingest is transactional, stopping it early is
 * not a thing to offer.
 */

const POLL_MS = 2000;
// Slower, but never zero. Stopping entirely at a terminal state left the screen
// frozen on whatever it last saw: a run begun in another tab, or after this page
// had settled, went unnoticed, and the dropzone stayed on offer over a job that
// was already running — an upload that could only answer 409. The status endpoint
// is a dict copy, so idling on it costs nothing worth saving.
const IDLE_POLL_MS = 10000;

export interface IngestJob {
  state: IngestState | null;
  /** True until the first status has come back — distinct from "nothing running". */
  loading: boolean;
  /** A client-side failure: the upload itself, or the server being unreachable. */
  error: string;
  start: (files: File[]) => Promise<void>;
  dismiss: () => void;
}

// `waiting` is deliberately not terminal: the job is alive and will start.
const TERMINAL = new Set(["idle", "done", "failed"]);

export function useIngestJob(): IngestJob {
  const [state, setState] = useState<IngestState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // WHICH run was dismissed, not merely that one was. As a bare boolean it
  // outlived the result it was set for: dismissing one outcome and then having a
  // second run finish — started in another tab, or by anything else on this
  // machine — left the flag still masking, so the new result never appeared. A
  // silently hidden *failure* is the version of that which costs something.
  const [dismissedRun, setDismissedRun] = useState<number | null>(null);
  const live = useRef(true);
  const timer = useRef<number | undefined>(undefined);

  // A NAMED function expression so the reschedule below refers to itself rather
  // than to the `poll` binding, which is still being initialised at that point.
  const poll = useCallback(async function run() {
    try {
      const res = await fetch("/api/ingest/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = (await res.json()) as IngestState;
      if (!live.current) return;
      setState(next);
      setLoading(false);
      timer.current = window.setTimeout(
        run,
        TERMINAL.has(next.status) ? IDLE_POLL_MS : POLL_MS,
      );
    } catch (e) {
      if (!live.current) return;
      setLoading(false);
      // Keep polling through a failed poll. The server being briefly unreachable
      // is not the same as the job being over, and a ten-minute run must not be
      // abandoned by its own watcher over one dropped request.
      setError(e instanceof Error ? e.message : String(e));
      timer.current = window.setTimeout(run, POLL_MS);
    }
  }, []);

  useEffect(() => {
    live.current = true;
    void poll();
    return () => {
      live.current = false;
      window.clearTimeout(timer.current);
    };
  }, [poll]);

  const start = useCallback(
    async (files: File[]) => {
      setError("");
      setDismissedRun(null);
      const body = new FormData();
      for (const f of files) body.append("files", f, f.name);
      try {
        const res = await fetch("/api/ingest/start", { method: "POST", body });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `HTTP ${res.status}`);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      // Ask immediately rather than waiting out a poll interval — the screen
      // should change the moment the upload is accepted.
      window.clearTimeout(timer.current);
      void poll();
    },
    [poll],
  );

  // Captured from the state at the moment of dismissal, so it can only ever
  // mask that one run.
  const dismiss = useCallback(
    () => setDismissedRun(state?.started_at ?? null),
    [state?.started_at],
  );

  const visible =
    state &&
    dismissedRun !== null &&
    state.started_at === dismissedRun &&
    TERMINAL.has(state.status)
      ? { ...state, status: "idle" as const, files: [] }
      : state;

  return { state: visible, loading, error, start, dismiss };
}
