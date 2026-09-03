/**
 * The shape of `GET /api/ingest/status`.
 *
 * Mirrors `backend/coursegen/app/main.py::ingest_status`. If that changes, this
 * changes in the same commit.
 *
 * ## Why this is polled and generation is streamed
 *
 * Generation costs ~51 s and a student watches it, so a stream that dies with the
 * connection is fine. Ingest costs ~10 minutes and nobody watches that — they
 * switch tabs, shut the laptop, come back. Server-held state is the only kind a
 * reloaded page can go and read. That is the whole reason for the difference.
 */

export type IngestStatus = "idle" | "queued" | "running" | "done" | "failed";

/** `reading` and `mapping` take seconds; `indexing` takes the ten minutes. */
export type IngestStage = "reading" | "mapping" | "indexing";

export type FileState = "queued" | "reading" | "read" | "failed";

export interface IngestFile {
  file: string;
  state: FileState;
  /** Present on `read`. */
  blocks?: number;
  source_type?: string;
  /** Present on `failed` — the parser's own message, safe to show. */
  reason?: string;
}

export interface IngestState {
  status: IngestStatus;
  stage: IngestStage | null;
  files: IngestFile[];
  /** Course-map nodes. Known once `mapping` finishes. */
  topics: number | null;
  /** Chunks to embed — the only number that sizes the long wait. */
  passages: number | null;
  nodes_ingested: number | null;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  /**
   * Derived server-side, deliberately: a reloaded tab must show the age of the
   * run, not the age of its own connection.
   */
  elapsed_seconds: number | null;
}
