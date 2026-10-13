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

function stubApi(paper: unknown = SHEET) {
  const calls: { url: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const ok = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url === "/api/paper") return ok(paper);
    if (url === "/api/attempts") return ok(RESULT);
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
});
