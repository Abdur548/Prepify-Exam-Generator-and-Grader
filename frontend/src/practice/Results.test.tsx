import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { Results } from "./Results";
import { RESULT } from "./fixtures";
import type { AttemptResult } from "./types";

afterEach(cleanup);

function setup(result: AttemptResult = RESULT, onContest = vi.fn().mockResolvedValue(undefined)) {
  const { container } = render(
    <Results result={result} onContest={onContest} onDone={vi.fn()} />,
  );
  return { ui: within(container), container, onContest };
}

const row = (container: HTMLElement, slot: string) =>
  within(container.querySelector(`[data-slot="${slot}"]`) as HTMLElement);

describe("Results", () => {
  it("reads the total against what has been marked, not the whole paper", () => {
    const { ui } = setup();
    expect(ui.getByText(/31 \/ 44 marked so far/)).toBeTruthy();
    expect(ui.queryByText(/31 \/ 60/)).toBeNull();
  });

  it("reads the total against the paper once everything is marked", () => {
    const done = { ...RESULT, totals: { ...RESULT.totals, unmarked: 0, marks_marked: 60 } };
    const { ui } = setup(done);
    expect(ui.getByText(/31 \/ 60/)).toBeTruthy();
  });

  it("asks for a second opinion only on a low-confidence mark", () => {
    const { container } = setup();
    expect(row(container, "B-01").getByText(/check with your teacher/i)).toBeTruthy();
    expect(row(container, "A-01").queryByText(/check with your teacher/i)).toBeNull();
  });

  it("says a model mark can be contested", () => {
    const { container } = setup();
    expect(row(container, "B-01").getByText(/marked by the model against your material — contest any mark/i)).toBeTruthy();
  });

  it("says plainly when the student disagrees with the answer key", () => {
    const contested: AttemptResult = {
      ...RESULT,
      items: [{ ...RESULT.items[0], source: "student", marks_awarded: 2,
                objective_contest: true, original: { marks_awarded: 0, source: "key" } }],
    };
    const { container } = setup(contested);
    expect(row(container, "A-01").getByText(/you disagree with the answer key/i)).toBeTruthy();
    expect(row(container, "A-01").getByText(/was 0/i)).toBeTruthy();
  });

  it("never calls a mark verified or correct", () => {
    const { container } = setup();
    expect(container.textContent).not.toMatch(/verified|correct/i);
  });

  it("points a material question at the page to revise", () => {
    const { container } = setup();
    expect(row(container, "A-01").getByText(/03_search\.pdf · p\.30/)).toBeTruthy();
  });

  it("points a synthesis question at no page", () => {
    const { container } = setup();
    expect(row(container, "C-01").getByText(/not in your material/i)).toBeTruthy();
    expect(row(container, "C-01").queryByText(/03_search/)).toBeNull();
  });

  it("shows an unanswered question as unanswered, not as zero", () => {
    const { container } = setup();
    expect(row(container, "C-01").getByText(/not answered/i)).toBeTruthy();
  });

  it("contests with the number typed", async () => {
    const { container, onContest } = setup();
    const r = row(container, "B-01");
    fireEvent.change(r.getByLabelText(/your mark/i), { target: { value: "3" } });
    fireEvent.click(r.getByRole("button", { name: /contest/i }));
    expect(onContest).toHaveBeenCalledWith("B-01", 3);
  });

  it("lets the student mark an unmarked answer themselves", () => {
    const { container, onContest } = setup();
    const r = row(container, "B-02");
    fireEvent.change(r.getByLabelText(/your mark/i), { target: { value: "4" } });
    fireEvent.click(r.getByRole("button", { name: /mark it yourself/i }));
    expect(onContest).toHaveBeenCalledWith("B-02", 4);
  });

  it("refuses a mark the question cannot carry", () => {
    const { container, onContest } = setup();
    const r = row(container, "B-01");
    fireEvent.change(r.getByLabelText(/your mark/i), { target: { value: "9" } });
    fireEvent.click(r.getByRole("button", { name: /contest/i }));
    expect(onContest).not.toHaveBeenCalled();
  });

  it("offers no contest on an unanswered question", () => {
    const { container } = setup();
    expect(row(container, "C-01").queryByRole("button", { name: /contest|mark it yourself/i })).toBeNull();
  });
});
