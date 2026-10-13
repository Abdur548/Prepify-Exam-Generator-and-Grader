import { useCallback, useEffect, useState } from "react";
import { BlueprintScreen } from "./plan/BlueprintScreen";
import { UploadScreen } from "./upload/UploadScreen";
import { ChatScreen } from "./chat/ChatScreen";
import { DisclosureScreen } from "./system/DisclosureScreen";
import { GeneratingScreen } from "./generate/GeneratingScreen";
import { Paper } from "./paper/Paper";
import { AnswerSheet } from "./practice/AnswerSheet";
import { ProgressScreen } from "./practice/ProgressScreen";
import { Results } from "./practice/Results";
import { ApiError, contestMark, getAttempt, remarkAttempt, submitAttempt } from "./practice/api";
import { clearDraft } from "./practice/drafts";
import type { AttemptResult, SheetDoc } from "./practice/types";
import type { Plan } from "./plan/types";
import type { RunOutcome } from "./generate/types";
import type { Paper as PaperDoc } from "./paper/types";
import "./styles/tokens.css";
import "./harness.css";

/**
 * Dev harness. Five screens: upload, the blueprint (with its free dry run), the
 * wait, the paper, and ask. All run against the real API rather than fixtures.
 */
type Screen =
  | "upload"
  | "blueprint"
  | "disclosure"
  | "generating"
  | "paper"
  | "chat"
  | "answer"
  | "results"
  | "progress";

// The gate lives in the server's process memory and resets on restart, so this is
// a hint about what the server probably thinks — never the authority. A stale
// `true` costs nothing: the stream answers 403 and routes straight back here.
const ACCEPTED_KEY = "prepify.disclosure";

export default function App() {
  const [screen, setScreen] = useState<Screen>("blueprint");
  const [run, setRun] = useState<{ blueprintId: string; plan: Plan; adaptive: boolean } | null>(null);
  // MVP 2: build the next paper on the student's answers so far.
  const [adaptive, setAdaptive] = useState(false);
  const [paper, setPaper] = useState<PaperDoc | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);
  const [fromMaterialOnly, setFromMaterialOnly] = useState(false);
  // Practice: the sheet being answered, and the marks that came back.
  const [sheet, setSheet] = useState<SheetDoc | null>(null);
  const [attempt, setAttempt] = useState<AttemptResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  // Answers waiting on consent. The server refused them with 403 and nothing was
  // stored, so they are held here and sent again, unchanged, once accepted.
  const [awaitingConsent, setAwaitingConsent] = useState<Record<string, string> | null>(null);
  const [accepted, setAccepted] = useState(() => {
    try {
      return sessionStorage.getItem(ACCEPTED_KEY) === "yes";
    } catch {
      // Private windows and blocked site data both throw here. Not being able to
      // remember is fine; it just means the gate is shown again.
      return false;
    }
  });

  const remember = useCallback(() => {
    setAccepted(true);
    try {
      sessionStorage.setItem(ACCEPTED_KEY, "yes");
    } catch {
      /* see above */
    }
  }, []);

  useEffect(() => {
    if (screen !== "paper" || paper) return;
    fetch("/api/paper")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setPaper)
      .catch(() => setPaper(null));
  }, [screen, paper]);

  // Identity-stable: the generating screen holds this in an effect dependency,
  // and a fresh closure each render would re-run the paper fetch.
  const handleDone = useCallback((doc: PaperDoc, result: RunOutcome) => {
    setPaper(doc);
    setOutcome(result);
    setScreen("paper");
  }, []);

  const takeSheet = useCallback((doc: SheetDoc) => {
    setSheet(doc);
    setSubmitError("");
    setScreen("answer");
  }, []);

  const submit = useCallback(
    async (responses: Record<string, string>) => {
      if (!sheet?.sheet_id) return;
      setSubmitting(true);
      setSubmitError("");
      try {
        const result = await submitAttempt(sheet.sheet_id, responses);
        // Only once the server has them: a failed submit keeps the draft.
        clearDraft(sheet.sheet_id);
        setAttempt(result);
        setScreen("results");
      } catch (e) {
        if (e instanceof ApiError && e.status === 403) {
          setAwaitingConsent(responses);
          setAccepted(false);
          setScreen("disclosure");
          return;
        }
        setSubmitError(e instanceof Error ? e.message : "Your answers were not sent.");
      } finally {
        setSubmitting(false);
      }
    },
    [sheet],
  );

  // Re-read the whole attempt after a contest rather than patching one item: the
  // totals depend on every mark, and recomputing them here would be a second
  // copy of the server's rule that could drift from it.
  const contest = useCallback(
    async (slotId: string, marks: number) => {
      if (!attempt) return;
      await contestMark(attempt.attempt_id, slotId, marks);
      setAttempt(await getAttempt(attempt.attempt_id));
    },
    [attempt],
  );

  const remark = useCallback(async () => {
    if (!attempt) return;
    setAttempt(await remarkAttempt(attempt.attempt_id));
  }, [attempt]);

  // Leaving mid-run is safe — the pipeline finishes regardless — but coming back
  // and pressing Generate again is not: the second run blocks on the process lock
  // and then spends the quota over. The nav closes for the duration.
  const busy = screen === "generating";

  // Every route into generation passes through here, so there is one place that
  // decides whether the gate has been met rather than a check per entry point.
  const startRun = useCallback(
    (next: { blueprintId: string; plan: Plan }) => {
      setRun({ ...next, adaptive });
      setPaper(null);
      setOutcome(null);
      setScreen(accepted ? "generating" : "disclosure");
    },
    [accepted, adaptive],
  );

  return (
    <div>
      <header className="harness__bar">
        <nav className="harness__nav">
          <button
            className={screen === "upload" ? "on" : ""}
            disabled={busy}
            onClick={() => setScreen("upload")}
          >
            Upload
          </button>
          <button
            className={screen === "blueprint" ? "on" : ""}
            disabled={busy}
            onClick={() => setScreen("blueprint")}
          >
            Blueprint
          </button>
          <button
            className={screen === "paper" ? "on" : ""}
            disabled={busy}
            onClick={() => setScreen("paper")}
          >
            Paper
          </button>
          <button
            className={screen === "chat" ? "on" : ""}
            disabled={busy}
            onClick={() => setScreen("chat")}
          >
            Ask
          </button>
          <button
            className={screen === "progress" ? "on" : ""}
            disabled={busy}
            onClick={() => setScreen("progress")}
          >
            Progress
          </button>
        </nav>
        {screen === "paper" && paper && (
          <label className="harness__filter">
            <input
              type="checkbox"
              checked={fromMaterialOnly}
              onChange={(e) => setFromMaterialOnly(e.target.checked)}
            />
            From my material only
          </label>
        )}
      </header>

      <main>
        {screen === "upload" && (
          <UploadScreen onDone={() => setScreen("blueprint")} />
        )}

        {screen === "chat" && <ChatScreen />}

        {screen === "blueprint" && (
          <BlueprintScreen
            onGenerate={(blueprintId, plan) => startRun({ blueprintId, plan })}
            adaptive={adaptive}
            onAdaptiveChange={setAdaptive}
          />
        )}

        {screen === "disclosure" && (
          <DisclosureScreen
            onAccepted={() => {
              remember();
              if (awaitingConsent) {
                const responses = awaitingConsent;
                setAwaitingConsent(null);
                setScreen("answer");
                void submit(responses);
                return;
              }
              setScreen(run ? "generating" : "blueprint");
            }}
            onBack={() => {
              if (awaitingConsent) {
                setAwaitingConsent(null);
                setScreen("answer");
                return;
              }
              setScreen("blueprint");
            }}
          />
        )}

        {screen === "generating" && run && (
          <GeneratingScreen
            blueprintId={run.blueprintId}
            adaptive={run.adaptive}
            plan={run.plan}
            onDone={handleDone}
            onBack={() => setScreen("blueprint")}
            // The server restarted mid-session and forgot. Our remembered `true`
            // was a guess; the 403 is the authority, so forget it and re-ask.
            onNeedsDisclosure={() => {
              setAccepted(false);
              try {
                sessionStorage.removeItem(ACCEPTED_KEY);
              } catch {
                /* nothing to forget */
              }
              setScreen("disclosure");
            }}
          />
        )}

        {screen === "paper" &&
          (paper ? (
            <div className="harness__main">
              {outcome && <RunNotice outcome={outcome} />}
              <TakeBar paper={paper} onTake={() => takeSheet(paper)} />
              <Paper paper={paper} fromMaterialOnly={fromMaterialOnly} />
            </div>
          ) : (
            <p style={{ padding: 32 }}>No paper generated yet.</p>
          ))}

        {screen === "answer" && sheet && (
          <div className="harness__main">
            <AnswerSheet
              sheet={sheet}
              submitting={submitting}
              error={submitError}
              onSubmit={submit}
            />
          </div>
        )}

        {screen === "progress" && (
          <div className="harness__main">
            <ProgressScreen
              onNextPaper={() => {
                setAdaptive(true);
                setScreen("blueprint");
              }}
            />
          </div>
        )}

        {screen === "results" && attempt && (
          <div className="harness__main">
            <Results
              result={attempt}
              onContest={contest}
              onRemark={remark}
              onDone={() => setScreen("progress")}
            />
          </div>
        )}
      </main>
    </div>
  );
}

/**
 * The way into practice. A paper whose archive failed is still readable but has
 * nothing to answer against, so it says so instead of offering a button that
 * would only ever fail.
 */
function TakeBar({ paper, onTake }: { paper: PaperDoc; onTake(): void }) {
  if (!paper.sheet_id) {
    return (
      <p className="takebar takebar--off" role="status">
        This paper can't be taken — it was not saved for answering. Generate it
        again to take it.
      </p>
    );
  }
  return (
    <div className="takebar">
      <button type="button" className="sheet__submit" onClick={onTake}>
        Take this paper
      </button>
      <span className="takebar__hint">
        Answer it here and get marks, with the page to revise for each question.
      </span>
    </div>
  );
}

/**
 * `degraded` is not an error and `empty` is not a failure. Both are 200s with a
 * real paper behind them, and collapsing either into "something went wrong" loses
 * the only thing the student needs to know: what came back, and what did not.
 */
function RunNotice({ outcome }: { outcome: RunOutcome }) {
  if (outcome.status === "ok") return null;

  const short = outcome.unfilled_slots.length;
  return (
    <div className={`notice notice--${outcome.status}`} role="status">
      <p className="notice__lead">
        {outcome.status === "degraded"
          ? `We stopped early. The paper below is real and usable${short ? `, with ${short} question${short === 1 ? "" : "s"} left unwritten` : ""}.`
          : "This run produced no questions. The paper below shows the shape it would have had."}
      </p>
      {outcome.warnings.length > 0 && (
        <ul className="notice__list">
          {outcome.warnings.slice(0, 3).map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
