import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import App from "../App";
import { RESULT, SHEET } from "./fixtures";

/**
 * The whole G1 loop through the real App: open the paper, take it, submit, see
 * marks. Every request is answered by a stub keyed on the URL, so what is under
 * test is the wiring between screens — the thing no component test can see.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

function stubApi(paper: unknown = SHEET, refuseFirstSubmit = false) {
  const calls: { url: string; body?: unknown }[] = [];
  let submits = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const ok = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url === "/api/paper") return ok(paper);
    if (url === "/api/disclosure/accept") return ok({ accepted: true });
    if (url === "/api/attempts") {
      submits += 1;
      if (refuseFirstSubmit && submits === 1) {
        return new Response(JSON.stringify({ detail: "Consent is needed before your answers are sent to the model." }),
          { status: 403, headers: { "Content-Type": "application/json" } });
      }
      return ok(RESULT);
    }
    if (url.endsWith("/remark")) return ok({ ...RESULT, totals: { ...RESULT.totals, unmarked: 0 } });
    if (url === "/api/blueprints") return ok(["quiz_default"]);
    if (url === "/api/preflight") return ok({ api_key: "ok", output_dir: "ok", weasyprint: "ok", qdrant: "ok", models: "ok", memory: "ok" });
    if (url === "/api/plan") {
      const req = init?.body ? JSON.parse(String(init.body)) : {};
      return ok({
        blueprint_id: "quiz_default", title: "Quiz", total_marks: 20, duration_minutes: 30,
        sections: [],
        summary: { slots_total: 4, slots_planned: 4, fill_ratio: 1, allocation_fidelity: 1,
                   marks_planned: 20, short_by_marks: 0, synthesis_items: 0, sources_used: [],
                   bloom_realised: {}, bloom_declared: null, warnings: [], estimated_calls: 1 },
        ...(req.adaptive ? { adaptive: { applied: true, tilted_nodes: 3, changed_slots: 2, reworded: 1, orphans: 0 } } : {}),
      });
    }
    if (url === "/api/progress") {
      return ok({ attempts: 1, due: 1, next_review: 1, warnings: [],
                  cards: { box_1: 1, box_2: 0, box_3: 0, box_4: 0, retired: 0 },
                  lectures: [{ lecture: "03_search.pdf", mastery: 0.5, tested_share: 0.25,
                               nodes_total: 4, nodes_tested: 1, nodes: [] }] });
    }
    if (url === `/api/attempts/${RESULT.attempt_id}`) return ok(RESULT);
    if (url.startsWith("/api/attempts/") && url.endsWith("/contest")) return ok(RESULT.items[1]);
    return new Response("{}", { status: 404 });
  }));
  return calls;
}

async function openPaper(ui: ReturnType<typeof within>) {
  fireEvent.click(ui.getByRole("button", { name: "Paper" }));
  return ui.findByRole("button", { name: /take this paper/i });
}

describe("taking a paper", () => {
  it("submits the answers and shows the marks", async () => {
    const calls = stubApi();
    const { container } = render(<App />);
    const ui = within(container);

    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByLabelText(/Queue/));
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));

    expect(await ui.findByText(/31 \/ 44 marked so far/)).toBeTruthy();
    const post = calls.find((c) => c.url === "/api/attempts");
    expect(post?.body).toEqual({
      sheet_id: SHEET.sheet_id,
      answers: [{ slot_id: "A-01", response: "B" }],
    });
  });

  it("clears the draft once the answers are in", async () => {
    stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByLabelText(/Queue/));
    expect(sessionStorage.getItem(`prepify.draft.${SHEET.sheet_id}`)).toContain("B");
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));
    await ui.findByText(/marked so far/);
    expect(sessionStorage.getItem(`prepify.draft.${SHEET.sheet_id}`)).toBeNull();
  });

  it("reloads the results after a contest, so the totals stay true", async () => {
    const calls = stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));
    await ui.findByText(/marked so far/);

    const row = within(container.querySelector('[data-slot="B-01"]') as HTMLElement);
    fireEvent.change(row.getByLabelText(/your mark/i), { target: { value: "3" } });
    fireEvent.click(row.getByRole("button", { name: /contest/i }));
    await waitFor(() =>
      expect(calls.filter((c) => c.url === `/api/attempts/${RESULT.attempt_id}`)).toHaveLength(1),
    );
  });

  it("does not offer a paper that could not be archived", async () => {
    stubApi({ ...SHEET, sheet_id: null });
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(ui.getByRole("button", { name: "Paper" }));
    expect(await ui.findByText(/can't be taken/i)).toBeTruthy();
    expect(ui.queryByRole("button", { name: /take this paper/i })).toBeNull();
  });

  it("asks for consent when the server needs it, then sends the same answers", async () => {
    const calls = stubApi(SHEET, true);
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByLabelText(/Queue/));
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "level by level" } });
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));

    fireEvent.click(await ui.findByRole("button", { name: /I understand/i }));
    expect(await ui.findByText(/marked so far/)).toBeTruthy();
    const posts = calls.filter((c) => c.url === "/api/attempts");
    expect(posts).toHaveLength(2);
    expect(posts[1].body).toEqual(posts[0].body);
  });

  it("retries marking from the results", async () => {
    const calls = stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));
    fireEvent.click(await ui.findByRole("button", { name: /retry marking/i }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/remark"))).toBe(true));
    await waitFor(() => expect(ui.queryByText(/marked so far/)).toBeNull());
  });

  it("opens the progress tab", async () => {
    stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(ui.getByRole("button", { name: "Progress" }));
    expect(await ui.findByText(/50% mastery · 25% of the material tested/)).toBeTruthy();
  });

  it("goes to progress when the student is done with their marks", async () => {
    stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(await openPaper(ui));
    fireEvent.click(ui.getByRole("button", { name: /submit answers/i }));
    fireEvent.click(await ui.findByRole("button", { name: /^done$/i }));
    expect(await ui.findByRole("heading", { name: /your progress/i })).toBeTruthy();
  });

  it("aims the next paper at weak topics from the progress tab", async () => {
    const calls = stubApi();
    const { container } = render(<App />);
    const ui = within(container);
    fireEvent.click(ui.getByRole("button", { name: "Progress" }));
    fireEvent.click(await ui.findByRole("button", { name: /next paper, aimed at my weak topics/i }));

    const box = (await ui.findByLabelText(/aim at my weak topics/i)) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(await ui.findByText(/2 questions moved toward your weak topics/i)).toBeTruthy();
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/plan" && (c.body as { adaptive?: boolean })?.adaptive === true)).toBe(true),
    );
  });
});
