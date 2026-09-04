import { useEffect, useState } from "react";

/**
 * Topic names from the corpus that is actually loaded.
 *
 * `GET /api/topics` returns the densest headings from the student's own slides,
 * by `instructional_mass`. It costs no quota and loads no model — it reads the
 * same course map the dry run reads.
 *
 * Failure is silent and returns nothing. These are examples in an empty state:
 * showing none is a fine outcome, and an error message about a suggestions
 * endpoint would be noise in front of a working chat box.
 */
export function useTopics(limit = 3): string[] {
  const [topics, setTopics] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    fetch(`/api/topics?limit=${limit}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((t: string[]) => live && setTopics(Array.isArray(t) ? t : []))
      .catch(() => {
        /* no examples is a fine empty state */
      });
    return () => {
      live = false;
    };
  }, [limit]);

  return topics;
}
