import { describe, expect, it } from "vitest";
import { readWarnings } from "./warnings";

/**
 * The raw strings are written for whoever is debugging the allocator. Three of
 * them filled the dry-run panel with `span-exhausted; falling through to node
 * '006d27…'` — jargon and a 40-character hash naming nothing a student has seen.
 */

const FALLTHROUGH = (sha: string) =>
  `Section 'A': allocated nodes span-exhausted; falling through to node '${sha}'.`;

describe("translating solver warnings", () => {
  it("collapses repeated fall-throughs into one counted sentence", () => {
    const { advisories } = readWarnings([
      FALLTHROUGH("006d276049d8173dc30201430600a093d24ca849"),
      FALLTHROUGH("00c209e436ed01ef9f1ddbdef0ee7fad4ad2727f"),
      FALLTHROUGH("00d1e2b03e7c1a0609056e4ca238029591da7807"),
    ]);

    expect(advisories).toHaveLength(1);
    expect(advisories[0].text).toContain("3 questions in Section A");
    expect(advisories[0].tone).toBe("info");
  });

  it("never leaks a node hash or internal vocabulary", () => {
    const { advisories } = readWarnings([
      FALLTHROUGH("006d276049d8173dc30201430600a093d24ca849"),
      "Section 'C': no nodes matched topic 'Bayesian networks' — zero token overlap with any candidate node; section skipped.",
      "Section 'D': no nodes match requires_flags_any=['has_code']; section skipped.",
      "Slot 'C-01' could not be filled: all spans exhausted.",
    ]);

    const all = advisories.map((a) => a.text).join(" ");
    expect(all).not.toMatch(/[0-9a-f]{16,}/);
    expect(all).not.toMatch(/span-exhausted|falling through|requires_flags_any/);
  });

  it("leads with the sections a student can act on", () => {
    const { advisories } = readWarnings([
      FALLTHROUGH("006d276049d8173dc30201430600a093d24ca849"),
      FALLTHROUGH("00c209e436ed01ef9f1ddbdef0ee7fad4ad2727f"),
      "Section 'C': no nodes matched topic 'Bayesian networks' — zero token overlap; section skipped.",
    ]);

    // Ordering is the point: `slice(0, 3)` on the raw list could push an empty
    // section off the panel behind two repetitions of a routine fall-through.
    expect(advisories[0].text).toContain("Section C was left empty");
    expect(advisories[0].text).toContain("Bayesian networks");
  });

  it("keeps an unrecognised warning, minus its hashes", () => {
    // §1D: a warning is never dropped for being unfamiliar. That is how a real
    // one goes missing the first time the solver learns to emit it.
    const { advisories } = readWarnings([
      "Some future warning about node '0123456789abcdef0123' nobody wrote a rule for.",
    ]);

    expect(advisories).toHaveLength(1);
    expect(advisories[0].text).toContain("Some future warning about a topic");
    expect(advisories[0].text).not.toMatch(/[0-9a-f]{16,}/);
  });

  it("returns every original line untouched", () => {
    const raw = [FALLTHROUGH("abc0123456789abcdef0"), "Slot 'A-01' could not be filled: all spans exhausted."];
    expect(readWarnings(raw).raw).toEqual(raw);
  });

  it("says nothing when the solver said nothing", () => {
    expect(readWarnings([]).advisories).toEqual([]);
  });
});
