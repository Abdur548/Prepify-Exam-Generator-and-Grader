import { useEffect, useState } from "react";
import { BlueprintScreen } from "./plan/BlueprintScreen";
import { Paper } from "./paper/Paper";
import type { Paper as PaperDoc } from "./paper/types";
import "./styles/tokens.css";
import "./harness.css";

/**
 * Dev harness. Two screens so far: the blueprint (with its free dry run) and the
 * paper. Both run against the real API rather than fixtures.
 */
type Screen = "blueprint" | "paper";

export default function App() {
  const [screen, setScreen] = useState<Screen>("blueprint");
  const [paper, setPaper] = useState<PaperDoc | null>(null);
  const [fromMaterialOnly, setFromMaterialOnly] = useState(false);

  useEffect(() => {
    if (screen !== "paper" || paper) return;
    fetch("/api/paper")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setPaper)
      .catch(() => setPaper(null));
  }, [screen, paper]);

  return (
    <div>
      <header className="harness__bar">
        <nav className="harness__nav">
          <button
            className={screen === "blueprint" ? "on" : ""}
            onClick={() => setScreen("blueprint")}
          >
            Blueprint
          </button>
          <button
            className={screen === "paper" ? "on" : ""}
            onClick={() => setScreen("paper")}
          >
            Paper
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
        {screen === "blueprint" ? (
          <BlueprintScreen onGenerate={() => setScreen("paper")} />
        ) : paper ? (
          <div className="harness__main">
            <Paper paper={paper} fromMaterialOnly={fromMaterialOnly} />
          </div>
        ) : (
          <p style={{ padding: 32 }}>No paper generated yet.</p>
        )}
      </main>
    </div>
  );
}
