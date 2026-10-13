import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { ProgressScreen } from "./ProgressScreen";
import { ApiError } from "./api";
import type { Progress } from "./types";

afterEach(cleanup);

const PROGRESS: Progress = {
  attempts: 3,
  due: 4,
  next_review: 2,
  cards: { box_1: 3, box_2: 1, box_3: 0, box_4: 0, retired: 1 },
  warnings: [],
  lectures: [
    {
      lecture: "03_search.pdf", mastery: 0.625, tested_share: 0.4, nodes_total: 12, nodes_tested: 5,
      nodes: [{ node_id: "n1", topic: "03_search.pdf › BFS", mastery: 0.25, answers: 2 }],
    },
    { lecture: "06_csp.pdf", mastery: null, tested_share: 0, nodes_total: 8, nodes_tested: 0, nodes: [] },
  ],
};

async function setup(data: Progress | Error = PROGRESS, handlers = {}) {
  const load = vi.fn(() => (data instanceof Error ? Promise.reject(data) : Promise.resolve(data)));
  const { container } = render(<ProgressScreen load={load} {...handlers} />);
  const ui = within(container);
  await ui.findByRole("heading", { name: /your progress/i });
  return { ui, container };
}

describe("ProgressScreen", () => {
  it("shows mastery beside how much of the lecture was tested", async () => {
    const { ui } = await setup();
    expect(await ui.findByText(/63% mastery · 40% of the material tested/)).toBeTruthy();
  });

  it("draws the bar at the mastery level", async () => {
    const { container, ui } = await setup();
    await ui.findByText(/63% mastery/);
    const bar = container.querySelector('[data-lecture="03_search.pdf"] .progress__fill') as HTMLElement;
    expect(bar.style.width).toBe("62.5%");
  });

  it("says an untested lecture is untested, not zero", async () => {
    const { ui } = await setup();
    expect(await ui.findByText(/not tested yet/i)).toBeTruthy();
    expect(ui.queryByText(/0% mastery/)).toBeNull();
  });

  it("offers the next review with its size", async () => {
    const onStartReview = vi.fn();
    const { ui } = await setup(PROGRESS, { onStartReview });
    fireEvent.click(await ui.findByRole("button", { name: /start review \(2\)/i }));
    expect(onStartReview).toHaveBeenCalledTimes(1);
  });

  it("offers no review when nothing is due", async () => {
    const { ui } = await setup({ ...PROGRESS, due: 0, next_review: 0 }, { onStartReview: vi.fn() });
    expect((await ui.findByRole("button", { name: /start review/i })).hasAttribute("disabled")).toBe(true);
  });

  it("shows the warnings", async () => {
    const { ui } = await setup({ ...PROGRESS, warnings: ["2 answers refer to material that is no longer ingested; they are left out."] });
    expect(await ui.findByText(/no longer ingested/)).toBeTruthy();
  });

  it("says what to do when there is no material yet", async () => {
    const { ui } = await setup(new ApiError(409, "No course material has been ingested yet. Upload your notes first."));
    expect(await ui.findByText(/Upload your notes first/)).toBeTruthy();
  });

  it("never calls a mastery figure verified or correct", async () => {
    const { container, ui } = await setup();
    await ui.findByText(/63% mastery/);
    expect(container.textContent).not.toMatch(/verified|correct/i);
  });
});
