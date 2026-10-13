import { afterEach, describe, expect, it, vi } from "vitest";
import { clearDraft, loadDraft, saveDraft } from "./drafts";

/**
 * A long written answer lost to an accidental refresh is the kind of thing that
 * makes a student stop using a tool. Drafts survive a reload — and when storage
 * is unavailable (private windows, blocked site data) the sheet still works, it
 * just cannot remember.
 */
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("answer drafts", () => {
  it("survive a reload", () => {
    saveDraft("s1", { "A-01": "B", "B-01": "A queue.\nFIFO." });
    expect(loadDraft("s1")).toEqual({ "A-01": "B", "B-01": "A queue.\nFIFO." });
  });

  it("are kept per sheet", () => {
    saveDraft("s1", { "A-01": "B" });
    expect(loadDraft("s2")).toEqual({});
  });

  it("can be cleared", () => {
    saveDraft("s1", { "A-01": "B" });
    clearDraft("s1");
    expect(loadDraft("s1")).toEqual({});
  });

  it("fall back to empty when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(loadDraft("s1")).toEqual({});
  });

  it("do not throw when storage refuses a write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => saveDraft("s1", { "A-01": "B" })).not.toThrow();
  });

  it("ignore anything that is not a text answer", () => {
    sessionStorage.setItem("prepify.draft.s1", JSON.stringify({ "A-01": "B", "B-01": 7, x: null }));
    expect(loadDraft("s1")).toEqual({ "A-01": "B" });
  });

  it("ignore corrupt storage", () => {
    sessionStorage.setItem("prepify.draft.s1", "{not json");
    expect(loadDraft("s1")).toEqual({});
  });
});
