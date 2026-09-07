/**
 * Solver warnings, said in a way a student can act on.
 *
 * The raw strings are written for whoever is debugging the allocator, and they
 * read like it:
 *
 *     Section 'A': allocated nodes span-exhausted; falling through to node
 *     '006d276049d8173dc30201430600a093d24ca849'.
 *
 * Three of those filled most of the dry-run panel. Every part of that line is
 * either jargon (`span-exhausted`, `falling through`) or a 40-character hash that
 * identifies nothing a student has ever seen. Worse, it fires **once per slot**,
 * so a paper with eleven fall-throughs emits eleven of them — restating, badly, a
 * number already two lines above it as `allocation_fidelity`.
 *
 * ## Nothing is discarded
 *
 * Never delete a warning for being noisy. These are not deleted —
 * they are still in the API response, the coverage report and the run manifest,
 * and the panel keeps every original behind a disclosure. What changes is which
 * of them leads.
 */

export interface Advisory {
  /** Plain language, no ids, no internal vocabulary. */
  text: string;
  /** `warn` reads as amber; `info` is a fact about the paper, not a problem. */
  tone: "warn" | "info";
}

const SECTION = /Section '([^']+)'/;
const TOPIC = /no nodes matched topic '([^']+)'/;

/**
 * Fold the raw list into at most a few sentences.
 *
 * Grouped by *kind and section* rather than listed, because the per-slot warnings
 * repeat verbatim except for a hash — so a list of them carries one fact and N
 * lines of noise.
 */
export function readWarnings(raw: string[]): {
  advisories: Advisory[];
  raw: string[];
} {
  const fallthrough = new Map<string, number>();
  const skipped: Advisory[] = [];
  const unfilled = new Map<string, number>();
  const unknown: string[] = [];

  for (const w of raw) {
    const section = w.match(SECTION)?.[1];

    if (w.includes("span-exhausted; falling through")) {
      if (section) fallthrough.set(section, (fallthrough.get(section) ?? 0) + 1);
      continue;
    }

    if (w.includes("could not be filled")) {
      const sec = w.match(/Slot '([^'-]+)/)?.[1] ?? "?";
      unfilled.set(sec, (unfilled.get(sec) ?? 0) + 1);
      continue;
    }

    const topic = w.match(TOPIC)?.[1];
    if (topic) {
      skipped.push({
        tone: "warn",
        text: `Section ${section} was left empty — your uploads don’t appear to cover “${topic}”. Add material on it, or pick a different paper.`,
      });
      continue;
    }

    if (w.includes("requires_flags_any")) {
      skipped.push({
        tone: "warn",
        // The flags are has_table / has_code / has_equation / has_figure. Named
        // by what they are rather than by their field names.
        text: `Section ${section} was left empty — none of your material has the kind of content it needs (a diagram, table, equation or code).`,
      });
      continue;
    }

    // Anything the translator does not recognise still reaches the student,
    // minus the hashes. Silently dropping an unfamiliar warning is how a real
    // one goes missing the first time the solver learns to emit it.
    unknown.push(
      w
        .replace(/node '[0-9a-f]{16,}'/g, "a topic")
        .replace(/'[0-9a-f]{16,}'/g, "a topic"),
    );
  }

  const advisories: Advisory[] = [...skipped];

  for (const [section, count] of fallthrough) {
    advisories.push({
      // Information, not a fault: the solver did the right thing, and the paper
      // is complete. What the student can act on is that more material on the
      // main topics would make it better.
      tone: "info",
      text: `${count} question${count === 1 ? "" : "s"} in Section ${section} come from topics beyond the ones ranked highest — your material on those ran thin.`,
    });
  }

  for (const [section, count] of unfilled) {
    advisories.push({
      tone: "warn",
      text: `${count} question${count === 1 ? "" : "s"} in Section ${section} could not be built at all — there wasn’t enough material left.`,
    });
  }

  for (const text of unknown) advisories.push({ tone: "warn", text });

  return { advisories, raw };
}
