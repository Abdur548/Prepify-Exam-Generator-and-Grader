import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { useGenerationRun } from "./useGenerationRun";

/**
 * Two defects live here that only a browser ever caught, plus audit item 4.
 *
 * The worst was a stale `live` flag: the run guarded its START against
 * StrictMode's double-invoke but kept `live` in the effect closure, so the
 * teardown set it false and the second pass — returning early — never set it
 * back. The server answered and every `setState` was dropped, leaving a frozen
 * screen and a clock stuck at 0:00 over a run that was working perfectly.
 * Nothing in the repo could see it, because nothing rendered the component.
 */

function ndjson(lines: string[], opts: { close?: boolean } = { close: true }) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const l of lines) controller.enqueue(encoder.encode(l + "\n"));
      if (opts.close !== false) controller.close();
      // Left open otherwise — that is the silent-stream case.
    },
  });
}

function Probe({ onState }: { onState: (s: ReturnType<typeof useGenerationRun>) => void }) {
  onState(useGenerationRun("quiz_default", "Test"));
  return null;
}

let last: ReturnType<typeof useGenerationRun>;
// Inside StrictMode DELIBERATELY. The stale-`live` bug needed exactly this:
// mount, effects, teardown, effects again. Rendered plainly, the teardown never
// happens and the regression is invisible — which is how it reached a browser.
const renderRun = () =>
  render(
    <StrictMode>
      <Probe onState={(s) => (last = s)} />
    </StrictMode>,
  );

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe("events reach the screen", () => {
  it("applies stage and result events", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(ndjson([
        JSON.stringify({ event: "stage", stage: "reading", state: "start" }),
        JSON.stringify({ event: "stage", stage: "reading", state: "done", topics: 571 }),
        JSON.stringify({ event: "result", status: "ok", fill_ratio: 1, allocation_fidelity: 1, unfilled_slots: [], warnings: [] }),
      ]), { status: 200 }),
    ));

    renderRun();
    await flush();
    await flush();

    // The regression: a stale `live` flag dropped every one of these.
    expect(last.completed).toContain("reading");
    expect(last.topics).toBe(571);
    expect(last.phase).toBe("done");
  });

  it("treats a 403 as needing the disclosure, not as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Disclosure must be accepted" }), { status: 403 }),
    ));

    renderRun();
    await flush();

    expect(last.phase).toBe("needs_disclosure");
  });

  it("fails when the stream closes with no terminal event", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(ndjson([
        JSON.stringify({ event: "stage", stage: "writing", state: "start" }),
      ]), { status: 200 }),
    ));

    renderRun();
    await flush();
    await flush();

    expect(last.phase).toBe("error");
    // The pipeline outlives the connection, so the paper may have landed.
    expect(last.errorHint).toMatch(/may have finished/i);
  });
});

describe("a stream that goes quiet", () => {
  it("gives up rather than waiting forever", async () => {
    // Audit item 4. The stream stays OPEN and stops sending — the case a
    // closed-stream check cannot see, and which left the rail live and the
    // clock climbing indefinitely.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(
        ndjson([JSON.stringify({ event: "stage", stage: "writing", state: "start" })], { close: false }),
        { status: 200 },
      ),
    ));

    // Faked BEFORE the render. Installed afterwards they cannot see a timeout
    // that was already scheduled with the real clock, and the test passes a
    // silent stream while asserting nothing — which is what the first version
    // of it did.
    vi.useFakeTimers();
    renderRun();
    await flush();
    expect(last.phase).not.toBe("error");

    await act(async () => { vi.advanceTimersByTime(121_000); });

    expect(last.phase).toBe("error");
    expect(last.error).toMatch(/stopped sending/i);
  });
});

describe("the silence deadline resets", () => {
  it("does not give up on a slow run that keeps reporting", async () => {
    // The re-arm, not just the arm. Without resetting on every event, a
    // generation longer than the limit would be killed mid-flight — a worse bug
    // than the one the deadline fixes, and one that only shows up on the
    // machines slow enough to need it.
    let push!: (line: string) => void;
    let close!: () => void;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        push = (l) => c.enqueue(encoder.encode(`${l}\n`));
        close = () => c.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));

    vi.useFakeTimers();
    renderRun();
    await flush();

    // Four quiet stretches, each just under the limit, each broken by an event.
    for (let i = 0; i < 4; i++) {
      await act(async () => { vi.advanceTimersByTime(110_000); });
      push(JSON.stringify({ event: "batch", phase: "writing", done: i + 1, total: 4 }));
      await flush();
      expect(last.phase, `gave up after ${(i + 1) * 110}s of a reporting run`).not.toBe("error");
    }

    expect(last.batch?.done).toBe(4);
    close();
  });
});
