import { useCallback, useEffect, useState } from "react";
import { BlueprintScreen } from "./plan/BlueprintScreen";
import { UploadScreen } from "./upload/UploadScreen";
import { ChatScreen } from "./chat/ChatScreen";
import { GeneratingScreen } from "./generate/GeneratingScreen";
import { Paper } from "./paper/Paper";
import type { Plan } from "./plan/types";
import type { RunOutcome } from "./generate/types";
import type { Paper as PaperDoc } from "./paper/types";
import "./styles/tokens.css";
import "./harness.css";

/**
 * Dev harness. Five screens: upload, the blueprint (with its free dry run), the
 * wait, the paper, and ask. All run against the real API rather than fixtures.
 */
type Screen = "upload" | "blueprint" | "generating" | "paper" | "chat";

export default function App() {
  const [screen, setScreen] = useState<Screen>("blueprint");
  const [run, setRun] = useState<{ blueprintId: string; plan: Plan } | null>(null);
  const [paper, setPaper] = useState<PaperDoc | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);
  const [fromMaterialOnly, setFromMaterialOnly] = useState(false);

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

  // Leaving mid-run is safe — the pipeline finishes regardless — but coming back
  // and pressing Generate again is not: the second run blocks on the process lock
  // and then spends the quota over. The nav closes for the duration.
  const busy = screen === "generating";

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
        </nav>
        {screen === "chat" && <ChatScreen />}

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

        {screen === "blueprint" && (
          <BlueprintScreen
            onGenerate={(blueprintId, plan) => {
              setRun({ blueprintId, plan });
              setPaper(null);
              setOutcome(null);
              setScreen("generating");
            }}
          />
        )}

        {screen === "generating" && run && (
          <GeneratingScreen
            blueprintId={run.blueprintId}
            plan={run.plan}
            onDone={handleDone}
            onBack={() => setScreen("blueprint")}
          />
        )}

        {screen === "paper" &&
          (paper ? (
            <div className="harness__main">
              {outcome && <RunNotice outcome={outcome} />}
              <Paper paper={paper} fromMaterialOnly={fromMaterialOnly} />
            </div>
          ) : (
            <p style={{ padding: 32 }}>No paper generated yet.</p>
          ))}
      </main>
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
