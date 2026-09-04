import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { ProvenanceCard } from "./ProvenanceCard";

/**
 * Audit item 8, and the reason this runner exists.
 *
 * The card used to claim every press on a question, so dragging across a stem to
 * select it moved the card instead — on the one screen a student is meant to read
 * closely. That is invisible to a test that calls functions: it only shows up
 * when a real element receives real pointer events, which is why these run in
 * jsdom rather than as unit tests over the handlers.
 */

// Explicit, and every query below is scoped to the render that produced it.
// Without this the first version of these tests queried `document` and found the
// PREVIOUS test's card still mounted — which produced results that contradicted
// each other: a 3px press appeared to start a drag while a 280px one appeared not
// to move at all. The component was fine; the harness was reading the wrong DOM.
afterEach(cleanup);

function setup(enabled = true) {
  const { container } = render(
    <ProvenanceCard
      enabled={enabled}
      sourceLabel="03_search.pdf · p.48"
      question={<p>What is an admissible heuristic?</p>}
      source={<p>The source passage.</p>}
    />,
  );
  const card = container.querySelector(".prov") as HTMLElement;
  // jsdom gives every element zero width, so the drag maths would divide by a
  // fallback and never reach the latch threshold.
  Object.defineProperty(card, "offsetWidth", { value: 400, configurable: true });
  const face = () => card.querySelector(".prov__face--question") as HTMLElement;
  return { card, face, ui: within(container) };
}

const press = (card: HTMLElement, x: number, y: number) =>
  fireEvent.pointerDown(card, { pointerId: 1, clientX: x, clientY: y, buttons: 1 });
const move = (card: HTMLElement, x: number, y: number) =>
  fireEvent.pointerMove(card, { pointerId: 1, clientX: x, clientY: y, buttons: 1 });
const release = (card: HTMLElement, x: number, y: number) =>
  fireEvent.pointerUp(card, { pointerId: 1, clientX: x, clientY: y });

describe("a press that is not a drag", () => {
  it("leaves the card alone below the threshold", () => {
    const { card, face } = setup();
    press(card, 100, 50);
    move(card, 103, 51); // 3px — a click, or the start of a selection
    expect(card.className).not.toContain("prov--dragging");
    expect(face().style.opacity).toBe("1");
  });

  it("leaves the card alone when the movement is mostly vertical", () => {
    const { card, face } = setup();
    press(card, 100, 50);
    move(card, 104, 140); // down the page — selecting several lines
    expect(card.className).not.toContain("prov--dragging");
    expect(face().style.opacity).toBe("1");
  });

  it("does not latch open when a short press is released", () => {
    const { card } = setup();
    press(card, 100, 50);
    move(card, 103, 51);
    release(card, 103, 51);
    expect(card.className).not.toContain("prov--open");
  });
});

describe("a real horizontal drag", () => {
  it("tracks the pointer", () => {
    const { card, face } = setup();
    press(card, 100, 50);
    move(card, 380, 55); // 280 of 400px
    expect(card.className).toContain("prov--dragging");
    expect(Number(face().style.opacity)).toBeLessThan(0.5);
  });

  it("latches open past halfway and springs back below it", () => {
    const { card } = setup();
    press(card, 100, 50);
    move(card, 380, 55);
    release(card, 380, 55);
    expect(card.className).toContain("prov--open");

    // Back past halfway, not merely backwards. From open, progress is
    // 1 + dx/width, so a 40px nudge lands at 0.9 and correctly STAYS open —
    // the first version of this test asserted otherwise and was wrong about the
    // arithmetic, not about the component.
    press(card, 300, 50);
    move(card, 60, 52); // -240 of 400 → 0.4, past the latch point
    release(card, 60, 52);
    expect(card.className).not.toContain("prov--open");
  });
});

describe("the keyboard path", () => {
  it("offers a button that opens the same state as the drag", () => {
    const { card, ui } = setup();
    fireEvent.click(ui.getByRole("button", { name: /where this came from/i }));
    expect(card.className).toContain("prov--open");
  });

  it("has no gesture and no button for a synthesis item", () => {
    const { card, ui } = setup(false);
    expect(ui.queryByRole("button")).toBeNull();
    press(card, 100, 50);
    move(card, 380, 55);
    expect(card.className).not.toContain("prov--dragging");
  });
});
