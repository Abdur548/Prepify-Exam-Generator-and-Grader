import { useRef, useState } from "react";
import type { IngestFile, IngestStage, IngestState } from "./types";
import { useIngestJob } from "./useIngestJob";
import { Blocked, Unreachable } from "../system/Blocked";
import { blockers, usePreflight } from "../system/usePreflight";
import "./upload.css";

/**
 * Add your lecture slides — and then wait about ten minutes.
 *
 * This screen's real subject is the wait, not the dropzone. Ingest is the longest
 * thing the product does by an order of magnitude, and the two honest things to
 * say about it are *which* stage is running and *how long it has been*. Both come
 * from the server, so both survive the tab being closed.
 *
 * ## What it will not do
 *
 * Show a progress bar for `indexing`. That stage is one
 * `model.encode(texts, batch_size=…)` call with no callback to hook, so a bar
 * moving across it would be a shape drawn from a clock — the exact thing the
 * generating screen was built to avoid. An elapsed count and an honest estimate
 * are what the system actually knows.
 *
 * ## Why the file list is the interesting part
 *
 * Parsing is the only stage that can say anything about a file a student would
 * recognise; after it, everything is sections and chunks. So a deck that could
 * not be read is named here, with the parser's reason, at the moment it fails —
 * rather than going missing from every paper generated later.
 */

const ACCEPT = ".pdf,.pptx,.docx";
const ALLOWED = [".pdf", ".pptx", ".docx"];
const MAX_BYTES = 100 * 1024 * 1024; // config.MAX_FILE_SIZE_BYTES

const STAGES: { id: IngestStage; label: string }[] = [
  { id: "reading", label: "Reading your files" },
  { id: "mapping", label: "Finding the topics" },
  { id: "indexing", label: "Indexing your material" },
];

interface Props {
  onDone?: () => void;
}

export function UploadScreen({ onDone }: Props) {
  const job = useIngestJob();
  const preflight = usePreflight();
  // Ingest makes no LLM call, so a missing API key does not stop it. It does need
  // the embedder, and `memory` is what decides whether the embedder can run.
  const stopped = blockers(preflight.checks, "ingest");
  const busy =
    job.state?.status === "queued" ||
    job.state?.status === "waiting" ||
    job.state?.status === "running";

  return (
    <div className="up">
      <header>
        <h1 className="up__title">Add your lecture slides</h1>
        <p className="up__sub">
          We&rsquo;ll build practice papers from them — every question traced back
          to the page it came from.
        </p>
      </header>

      {job.loading ? (
        <p className="up__checking">Checking whether anything is already running…</p>
      ) : busy ? (
        <Running state={job.state!} />
      ) : (
        <>
          {preflight.unreachable ? (
            <Unreachable onRetry={preflight.refresh} />
          ) : stopped.length > 0 ? (
            <Blocked
              blockers={stopped}
              action="index your material"
              onRetry={preflight.refresh}
            />
          ) : (
            <Dropzone onFiles={job.start} error={job.error} />
          )}
          {job.state && job.state.status !== "idle" && (
            <Finished state={job.state} onDismiss={job.dismiss} onDone={onDone} />
          )}
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- dropzone -- */

function Dropzone({
  onFiles,
  error,
}: {
  onFiles: (files: File[]) => void;
  error: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);

  // Checked here as well as on the server, because the alternative is uploading
  // a 200 MB file over a slow link to be told at the end that it was never going
  // to be read.
  const accept = (list: FileList | null) => {
    if (!list) return;
    const ok: File[] = [];
    const bad: string[] = [];
    for (const f of Array.from(list)) {
      const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED.includes(ext)) bad.push(`${f.name} — we can't read ${ext} files`);
      else if (f.size > MAX_BYTES) bad.push(`${f.name} — over the 100 MB limit`);
      else ok.push(f);
    }
    setRejected(bad);
    if (ok.length) onFiles(ok);
  };

  return (
    <>
      <div
        className={`drop${over ? " drop--over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          accept(e.dataTransfer.files);
        }}
      >
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="drop__input"
          onChange={(e) => {
            accept(e.target.files);
            e.target.value = "";
          }}
        />
        <p className="drop__lead">Drop your slides here</p>
        <button
          type="button"
          className="drop__btn"
          onClick={() => input.current?.click()}
        >
          Choose files
        </button>
        {/* Scholarly's pattern: the limits sit in the zone, not behind a tooltip
            that is read after the failure. */}
        <p className="drop__limits">PDF · PowerPoint · Word — up to 100 MB each</p>
      </div>

      <p className="up__warn">
        Indexing a full course takes about ten minutes, and only one upload can be
        indexed at a time — so add everything in one go.
      </p>

      {rejected.length > 0 && (
        <ul className="up__rejected">
          {rejected.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}

      {error && <p className="up__error">{error}</p>}
    </>
  );
}

/* --------------------------------------------------------------- running -- */

function Running({ state }: { state: IngestState }) {
  const current = state.stage;
  const reached = current ? STAGES.findIndex((s) => s.id === current) : -1;

  return (
    <section className="run" aria-live="polite">
      <header className="run__head">
        <h2 className="run__title">Indexing your material</h2>
        <span className="run__clock">{formatElapsed(state.elapsed_seconds)}</span>
      </header>

      {/* Queued, not started. Without this the three stages sit pending under a
          climbing clock with nothing to say why — the same silence the exam
          stream's `waiting` event was added to break. */}
      {state.status === "waiting" && (
        <p className="run__queued">
          Waiting for the server to finish {state.waiting_for ?? "another job"}.
          Your upload starts as soon as it does.
        </p>
      )}

      <ol className="run__stages">
        {STAGES.map((s, i) => (
          <li
            key={s.id}
            className={`rstep${i < reached ? " rstep--done" : ""}${
              s.id === current ? " rstep--active" : ""
            }`}
          >
            <span className="rstep__dot" aria-hidden="true" />
            <span className="rstep__label">{s.label}</span>
            <span className="rstep__note">{stageNote(s.id, state)}</span>
          </li>
        ))}
      </ol>

      {state.files.length > 0 && <FileList files={state.files} />}

      {/* The payoff of a server-held job, said out loud. Without this line a
          student sits and watches a page for ten minutes because nothing told
          them they did not have to. */}
      <p className="run__safe">
        This keeps running if you close the tab. Come back to this page any time
        to see where it got to.
      </p>
    </section>
  );
}

/** Only ever states what the server has already reported. */
function stageNote(stage: IngestStage, state: IngestState): string {
  if (stage === "reading") {
    const done = state.files.filter((f) => f.state !== "queued" && f.state !== "reading").length;
    return state.files.length ? `${done} of ${state.files.length} files` : "";
  }
  if (stage === "mapping") {
    return state.topics !== null ? `${state.topics} topics` : "";
  }
  if (state.stage !== "indexing") return "";
  // No sub-progress exists for this stage — see the file header. A passage count
  // and a rough estimate are what we actually have.
  return state.passages !== null
    ? `${state.passages} passages · ${indexEstimate(state.passages)}`
    : "this is the slow part";
}

/**
 * How long indexing is likely to take, from the passage count.
 *
 * Two measurements, one at each end: 572 passages in ~10 minutes
 * (`API-CONTRACT.md`) and 2 passages in 28 s (measured 2026-09-04), of which ~25 s
 * was the model load. `30 + passages` seconds fits both closely.
 *
 * Two points is not a model, so the buckets are coarse and the copy hedges. The
 * fixed "about ten minutes" this replaces was measured on a full course and
 * printed regardless of size — it told a student with three slides to expect ten
 * minutes of a wait that took under one, which is the same class of error as a
 * progress bar drawn from a clock.
 */
function indexEstimate(passages: number): string {
  const seconds = 30 + passages;
  if (seconds < 90) return "under a minute";
  if (seconds < 300) return `roughly ${Math.round(seconds / 60)} minutes`;
  return `roughly ${Math.round(seconds / 60)} minutes — this is the slow part`;
}

function FileList({ files }: { files: IngestFile[] }) {
  return (
    <ul className="files">
      {files.map((f) => (
        <li key={f.file} className={`frow frow--${f.state}`}>
          <span className="frow__name">{f.file}</span>
          <span className="frow__state">
            {f.state === "read"
              ? `${f.blocks ?? 0} blocks`
              : f.state === "failed"
                ? (f.reason ?? "could not be read")
                : f.state === "reading"
                  ? "reading…"
                  : "waiting"}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* -------------------------------------------------------------- finished -- */

function Finished({
  state,
  onDismiss,
  onDone,
}: {
  state: IngestState;
  onDismiss: () => void;
  onDone?: () => void;
}) {
  const failedFiles = state.files.filter((f) => f.state === "failed");

  if (state.status === "failed") {
    return (
      <div className="done done--bad">
        <p className="done__lead">{state.error}</p>
        <p className="done__hint">
          Your files were not kept. Nothing about your existing material changed.
        </p>
        <button type="button" className="done__btn" onClick={onDismiss}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="done">
      <p className="done__lead">
        Indexed <strong>{state.nodes_ingested ?? 0}</strong> topics from{" "}
        {state.files.length - failedFiles.length} file
        {state.files.length - failedFiles.length === 1 ? "" : "s"} in{" "}
        {formatElapsed(state.elapsed_seconds)}.
      </p>

      {/* Surfaced on the success screen, not only mid-run: this is the last
          moment a student can notice a deck is missing before they start
          wondering why their paper never mentions it. */}
      {failedFiles.length > 0 && (
        <div className="done__skipped">
          <p>
            {failedFiles.length} file{failedFiles.length === 1 ? "" : "s"} could not
            be read and {failedFiles.length === 1 ? "was" : "were"} left out:
          </p>
          <ul>
            {failedFiles.map((f) => (
              <li key={f.file}>
                <span className="frow__name">{f.file}</span> — {f.reason ?? "unreadable"}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="done__acts">
        <button type="button" className="done__btn done__btn--go" onClick={onDone}>
          Build a paper
        </button>
        <button type="button" className="done__btn" onClick={onDismiss}>
          Add more files
        </button>
      </div>
    </div>
  );
}

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return "0:00";
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
