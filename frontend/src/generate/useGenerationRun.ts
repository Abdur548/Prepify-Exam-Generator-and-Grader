import { useEffect, useRef, useState } from "react";
import type { BatchEvent, ResultEvent, RunEvent, StageId } from "./types";

/**
 * Drives one generation and reports what the backend is actually doing.
 *
 * ## Why there is no cancel
 *
 * Aborting the fetch closes the connection and changes nothing on the server: the
 * pipeline runs in its own thread and must finish, because it holds the process
 * lock and the artifacts on disk only agree with the manifest once render has
 * completed. A Cancel button would therefore stop the *narration* while the run,
 * and the student's quota, carried on. So there isn't one. Navigating away is
 * safe for the same reason — the paper still lands, and `GET /api/paper` has it.
 *
 * ## Why the start is guarded by a ref
 *
 * StrictMode mounts, runs effects, tears down and runs them again. A second POST
 * does not fail and does not queue visibly — it blocks on the pipeline lock and
 * then runs a *complete second generation*, at roughly double the quota and
 * double the wait. The guard makes the run start exactly once per mount.
 */

/**
 * How long the stream may say nothing before it is treated as dead.
 *
 * A stream that CLOSES without a terminal event has been handled since
 * 2026-09-03. A stream that stays open and goes silent was not: the stage rail
 * stayed live and the clock kept climbing with nothing ever resolving, and a
 * student had no way to tell "still writing questions" from "gone".
 *
 * Sized off the longest legitimate gap, which is the model load: measured at
 * 24.5 s cold, and the pipeline says nothing between `loading` start and its
 * completion. 120 s is roughly five times that, so a slow machine is never cut
 * off — the protocol has no heartbeat, so this has to be generous or it becomes
 * a worse bug than the one it fixes.
 */
const SILENCE_LIMIT_MS = 120_000;

export type RunPhase =
  | "connecting"
  | "running"
  | "done"
  | "error"
  | "needs_disclosure";

export interface RunState {
  phase: RunPhase;
  /** Preconditions, live only while they are actually happening. */
  waiting: boolean;
  loading: boolean;
  /** Stages that have completed, in order of completion. */
  completed: StageId[];
  /** The stage currently in flight, or null between stages. */
  active: StageId | null;
  /** Facts each stage reported when it finished. Shown, never invented. */
  topics: number | null;
  slots: number | null;
  sections: number | null;
  batch: BatchEvent | null;
  result: ResultEvent | null;
  error: string;
  /** What the student can do about it. Differs by failure, so it is not copy. */
  errorHint: string;
  /** Seconds since the request went out. */
  elapsed: number;
}

const INITIAL: RunState = {
  phase: "connecting",
  waiting: false,
  loading: false,
  completed: [],
  active: null,
  topics: null,
  slots: null,
  sections: null,
  batch: null,
  result: null,
  error: "",
  errorHint: "",
  elapsed: 0,
};

export function useGenerationRun(blueprintId: string, title: string): RunState {
  const [state, setState] = useState<RunState>(INITIAL);
  const started = useRef(false);
  const startedAt = useRef(0);
  // A ref, not a closure variable, and this is the whole subtlety.
  //
  // The first version guarded the *start* with `started` but kept `live` inside
  // the effect. StrictMode then ran the effect, tore it down — setting `live`
  // false and killing the ticker — and ran it again, where the guard returned
  // early and rebuilt neither. The request went out, the server answered, and
  // every `setState` was dropped by a flag nothing would ever set back to true:
  // a screen frozen on the first frame with a clock stuck at 0:00, over a run
  // that was working perfectly. Caught by opening it in a browser, not by any
  // test here — the tests never render the component.
  //
  // As a ref it is re-armed by each effect run and only finally cleared by the
  // real unmount, which is what "is this component still mounted" actually means.
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    if (!startedAt.current) startedAt.current = Date.now();

    const ticker = window.setInterval(() => {
      if (live.current) {
        setState((s) => ({
          ...s,
          elapsed: Math.floor((Date.now() - startedAt.current) / 1000),
        }));
      }
    }, 1000);

    // Reset by every event, so the deadline measures SILENCE rather than total
    // run time — a long generation is fine; a quiet one is not.
    let silence: number | undefined;
    const giveUp = () => {
      if (!live.current) return;
      setState((s) =>
        s.phase === "done" || s.phase === "error"
          ? s
          : {
              ...s,
              phase: "error",
              error: "The server stopped sending updates part-way through.",
              errorHint:
                "It may still be working. Check the paper before generating again — a second run costs the same as the first.",
            },
      );
    };
    const armSilence = () => {
      window.clearTimeout(silence);
      silence = window.setTimeout(giveUp, SILENCE_LIMIT_MS);
    };

    const apply = (e: RunEvent) => {
      armSilence();
      setState((s) => (live.current ? reduce(s, e) : s));
    };

    if (started.current) {
      return () => {
        live.current = false;
        window.clearInterval(ticker);
        window.clearTimeout(silence);
      };
    }
    started.current = true;

    (async () => {
      try {
        const res = await fetch("/api/exam/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ blueprint_id: blueprintId, title }),
        });

        // A non-200 is JSON, not NDJSON — the request never got as far as
        // streaming. 403 is its own outcome: the disclosure gate resets whenever
        // the server restarts, so it is a normal thing to meet, not a bug.
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          if (!live.current) return;
          setState((s) => ({
            ...s,
            phase: res.status === 403 ? "needs_disclosure" : "error",
            error: body.detail || `HTTP ${res.status}`,
            errorHint:
              "Nothing was written. Your material is untouched, and trying again costs the same as the first attempt.",
          }));
          return;
        }
        if (!res.body) throw new Error("This browser cannot read a streamed response.");

        setState((s) => (live.current ? { ...s, phase: "running" } : s));
        armSilence();
        await readNdjson(res.body, apply);
        window.clearTimeout(silence);

        // The stream ended. If it ended without `result` or `error`, the server
        // went away mid-run — the documented case is the OS killing the process
        // during the model load, which is an access violation no handler can
        // catch and therefore cannot arrive as an error event. Without this the
        // screen would sit on a live-looking stage rail forever.
        setState((s) =>
          !live.current || s.phase === "done" || s.phase === "error"
            ? s
            : {
                ...s,
                phase: "error",
                error:
                  "The connection ended before the run reported an outcome.",
                // Deliberately NOT "nothing was written". The pipeline runs in
                // its own thread and does not stop when the connection does, so
                // the paper may well have landed — saying otherwise would send
                // the student to regenerate something they already have.
                errorHint:
                  "The run may have finished anyway. Check the paper before generating again — a second run costs the same as the first.",
              },
        );
      } catch (err) {
        if (!live.current) return;
        setState((s) => ({
          ...s,
          phase: "error",
          error: err instanceof Error ? err.message : String(err),
          errorHint:
            "Nothing was written. Your material is untouched, and trying again costs the same as the first attempt.",
        }));
      }
    })();

    return () => {
      live.current = false;
      window.clearInterval(ticker);
      window.clearTimeout(silence);
    };
  }, [blueprintId, title]);

  return state;
}

function reduce(s: RunState, e: RunEvent): RunState {
  switch (e.event) {
    case "stage": {
      if (e.stage === "waiting") return { ...s, waiting: e.state === "start" };
      if (e.stage === "loading") return { ...s, loading: e.state === "start" };
      if (e.state === "start") return { ...s, active: e.stage };
      return {
        ...s,
        active: s.active === e.stage ? null : s.active,
        completed: s.completed.includes(e.stage)
          ? s.completed
          : [...s.completed, e.stage],
        topics: e.topics ?? s.topics,
        slots: e.slots ?? s.slots,
        sections: e.sections ?? s.sections,
      };
    }
    case "batch":
      return { ...s, batch: e };
    case "result":
      return { ...s, phase: "done", active: null, result: e };
    case "error":
      return {
        ...s,
        phase: "error",
        active: null,
        error: e.detail,
        errorHint:
          "Nothing was written. Your material is untouched, and trying again costs the same as the first attempt.",
      };
    default:
      return s;
  }
}

/**
 * Read newline-delimited JSON off a stream.
 *
 * A line that will not parse is skipped rather than thrown, because one malformed
 * frame must not cost the terminal `result` event that follows it — the paper is
 * on disk either way, and losing the event would strand a finished run.
 */
async function readNdjson(
  body: ReadableStream<Uint8Array>,
  onEvent: (e: RunEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flush = (line: string) => {
    const text = line.trim();
    if (!text) return;
    try {
      onEvent(JSON.parse(text) as RunEvent);
    } catch {
      /* partial or malformed frame — see above */
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      flush(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  flush(buffer);
}
