import { useEffect, useState } from "react";

/**
 * Take the paper away with you.
 *
 * ## What is offered, and what is deliberately not
 *
 * The backend renders five files into `output/`. Three are offered here:
 * `exam.pdf`, `answer_key.pdf` and `exam.html`.
 *
 * **`coverage.html` is not**, and must not be. It contains `coverage_ratio` —
 * the metric measured as inverted, which read **0.04 on a paper that was 20/20
 * items and 100/100 marks** and which favours a random baseline on 2 of 4
 * blueprints. `API-CONTRACT.md` says never render it or reconstruct an
 * equivalent; linking the page that prints it would be doing exactly that at one
 * remove, and a student who opened it would conclude their paper was worthless.
 *
 * **There is no Word export.** `UI-DESIGN.md` §5.4 asks for "Download Word", and
 * `/api/files/exam.docx` returns 404 because nothing renders one. A button for it
 * would be a button that fails, so the gap is left visible rather than papered
 * over.
 *
 * ## Why availability is probed
 *
 * A run that failed part-way can leave `paper.json` on disk with no PDFs beside
 * it, and `/api/files` has no HEAD (it answers 405), so a link cannot be checked
 * cheaply the obvious way. A GET resolves as soon as headers arrive; cancelling
 * the body then throws the download away without reading it.
 */

interface Download {
  file: string;
  label: string;
  hint: string;
}

const OFFERED: Download[] = [
  { file: "exam.pdf", label: "Paper", hint: "PDF" },
  { file: "answer_key.pdf", label: "Answer key", hint: "PDF" },
  { file: "exam.html", label: "Paper", hint: "web page" },
];

export function PaperActions() {
  const [available, setAvailable] = useState<Set<string>>(new Set());
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();

    Promise.all(
      OFFERED.map(async (d) => {
        try {
          const res = await fetch(`/api/files/${d.file}`, {
            signal: controller.signal,
            // Without this the probe answers from the HTTP cache and reports a
            // file that is gone as present — observed by deleting answer_key.pdf
            // and watching its link survive a reload. A cached probe is not a
            // probe; it is a memory of a probe.
            cache: "no-store",
          });
          // Headers are in; the body is not wanted. Cancelling here is what keeps
          // this a probe rather than three silent downloads.
          void res.body?.cancel();
          return res.ok ? d.file : null;
        } catch {
          return null;
        }
      }),
    ).then((found) => {
      if (!live) return;
      setAvailable(new Set(found.filter((f): f is string => f !== null)));
      setChecked(true);
    });

    return () => {
      live = false;
      controller.abort();
    };
  }, []);

  const ready = OFFERED.filter((d) => available.has(d.file));
  if (!checked || ready.length === 0) return null;

  return (
    <div className="acts" aria-label="Download this paper">
      {ready.map((d) => (
        <a
          key={d.file}
          className="acts__link"
          href={`/api/files/${d.file}`}
          download={d.file}
        >
          {d.label}
          <span className="acts__hint">{d.hint}</span>
        </a>
      ))}
    </div>
  );
}
