import { useEffect, useState } from "react";
import { Paper } from "./paper/Paper";
import type { Paper as PaperDoc } from "./paper/types";
import "./styles/tokens.css";
import "./harness.css";

/**
 * Dev harness for the paper renderer.
 *
 * Loads the REAL paper.json produced by a live generation rather than a fixture,
 * because a renderer that only works against hand-written data is a renderer that
 * has not been tested. Swapped for `GET /api/paper` when the app shell lands.
 */
export default function App() {
  const [paper, setPaper] = useState<PaperDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fromMaterialOnly, setFromMaterialOnly] = useState(false);

  useEffect(() => {
    fetch("/paper.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setPaper)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p style={{ padding: 32 }}>Could not load the paper: {error}</p>;
  if (!paper) return <p style={{ padding: 32 }}>Loading…</p>;

  const { summary } = paper;

  return (
    <div className="harness">
      <header className="harness__bar">
        <div className="harness__stats">
          <b>{summary.items_total}</b> questions
          <span>·</span>
          <b>{summary.marks_available}</b>/{paper.total_marks} marks
          <span>·</span>
          fill <b>{Math.round(summary.fill_ratio * 100)}%</b>
          {summary.synthesis_items > 0 && (
            <>
              <span>·</span>
              <b>{summary.synthesis_items}</b> not from your material
            </>
          )}
        </div>
        <label className="harness__filter">
          <input
            type="checkbox"
            checked={fromMaterialOnly}
            onChange={(e) => setFromMaterialOnly(e.target.checked)}
          />
          From my material only
        </label>
      </header>
      <main className="harness__main">
        <Paper paper={paper} fromMaterialOnly={fromMaterialOnly} />
      </main>
    </div>
  );
}
