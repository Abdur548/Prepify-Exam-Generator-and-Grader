import { useCallback, useEffect, useState } from "react";

/**
 * The six checks `GET /api/preflight` reports, and what they gate.
 *
 * ## Why this exists at all
 *
 * `memory` is the one that will bite. BGE-M3 needs about 4 GB of committable
 * memory; below that the OS kills the process with an access violation — not an
 * exception, not a degraded response, nothing any handler can catch and nothing
 * any screen can report after the fact. The only place that failure can be
 * prevented is *before* the button is pressed, which is why `API-CONTRACT.md`
 * says to block Generate and Chat on `memory` and not merely on `models`.
 *
 * `models: "ok"` beside a failing `memory` means the files are on disk and the
 * machine still cannot run them. Those are different questions and the UI must
 * not collapse them.
 */

export type CheckName =
  | "api_key"
  | "output_dir"
  | "models"
  | "memory"
  | "weasyprint"
  | "qdrant";

/** Each value is `"ok"` or a human-readable failure string. Never an exception. */
export type Preflight = Record<CheckName, string>;

/**
 * What each action actually needs. Listed per action rather than as one global
 * "is everything fine", because a machine with no WeasyPrint can still answer
 * questions perfectly well, and blocking Ask on that would be a lie about what
 * is broken.
 */
export const NEEDS: Record<"generate" | "chat" | "ingest", CheckName[]> = {
  // Writes artifacts and renders PDFs, so it needs the whole set.
  generate: ["api_key", "models", "memory", "qdrant", "output_dir", "weasyprint"],
  // Retrieves, reranks and calls the model. No rendering, no output directory.
  chat: ["api_key", "models", "memory", "qdrant"],
  // Embeds and writes Qdrant. No LLM call, so no API key.
  ingest: ["models", "memory", "qdrant"],
};

export interface PreflightState {
  checks: Preflight | null;
  loading: boolean;
  /** Reaching the endpoint failed — distinct from a check reporting a failure. */
  unreachable: boolean;
  refresh: () => void;
}

// Shared across screens. Preflight costs ~0.22 s warm but ~4.97 s cold, because
// it imports WeasyPrint and that drags in GTK — so three screens each fetching
// their own would be up to fifteen seconds of nothing on a cold process.
let cached: Promise<Preflight> | null = null;

function load(force = false): Promise<Preflight> {
  if (force || !cached) {
    cached = fetch("/api/preflight").then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<Preflight>;
    });
    // A rejected promise must not become the permanent answer: the server may
    // simply have been starting up.
    cached.catch(() => {
      cached = null;
    });
  }
  return cached;
}

export function usePreflight(): PreflightState {
  const [checks, setChecks] = useState<Preflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [unreachable, setUnreachable] = useState(false);

  const run = useCallback((force: boolean) => {
    setLoading(true);
    setUnreachable(false);
    return load(force)
      .then((c) => setChecks(c))
      .catch(() => setUnreachable(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    let live = true;
    load(false)
      .then((c) => live && setChecks(c))
      .catch(() => live && setUnreachable(true))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  return { checks, loading, unreachable, refresh: () => void run(true) };
}

/** The failing checks that stand between the student and this action. */
export function blockers(
  checks: Preflight | null,
  action: keyof typeof NEEDS,
): { name: CheckName; reason: string }[] {
  if (!checks) return [];
  return NEEDS[action]
    .filter((name) => checks[name] && checks[name] !== "ok")
    .map((name) => ({ name, reason: checks[name] }));
}

/** Plain-language names. The raw keys are ours, not the student's. */
export const CHECK_LABELS: Record<CheckName, string> = {
  api_key: "Model access",
  output_dir: "Somewhere to save",
  models: "Model files",
  memory: "Free memory",
  weasyprint: "PDF rendering",
  qdrant: "Search index",
};
