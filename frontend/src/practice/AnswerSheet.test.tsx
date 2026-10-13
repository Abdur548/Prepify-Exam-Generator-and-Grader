import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { AnswerSheet } from "./AnswerSheet";
import { saveDraft } from "./drafts";
import { SHEET } from "./fixtures";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

function setup(onSubmit = vi.fn()) {
  const { container } = render(
    <AnswerSheet sheet={SHEET} submitting={false} onSubmit={onSubmit} />,
  );
  return { ui: within(container), container, onSubmit };
}

describe("AnswerSheet", () => {
  it("offers a choice for each option of a multiple-choice question", () => {
    const { ui } = setup();
    expect(ui.getByLabelText(/Stack/)).toHaveProperty("type", "radio");
    expect(ui.getByLabelText(/Queue/)).toHaveProperty("type", "radio");
  });

  it("offers a text box for a written question", () => {
    const { container } = setup();
    expect(container.querySelectorAll("textarea")).toHaveLength(1);
  });

  it("does not offer an unfilled slot as a question", () => {
    const { container } = setup();
    expect(container.querySelectorAll("[data-slot]")).toHaveLength(2);
    expect(container.querySelector('[data-slot="A-02"]')).toBeNull();
  });

  it("submits each answer under its slot", () => {
    const { ui, container, onSubmit } = setup();
    fireEvent.click(ui.getByLabelText(/Queue/));
    fireEvent.change(container.querySelector("textarea")!, {
      target: { value: "It explores level by level." },
    });
    fireEvent.click(ui.getByRole("button", { name: /submit/i }));
    expect(onSubmit).toHaveBeenCalledWith({
      "A-01": "B",
      "B-01": "It explores level by level.",
    });
  });

  it("restores a saved draft", () => {
    saveDraft(SHEET.sheet_id!, { "A-01": "A", "B-01": "half an answer" });
    const { ui, container } = setup();
    expect(ui.getByLabelText(/Stack/)).toHaveProperty("checked", true);
    expect(container.querySelector("textarea")!.value).toBe("half an answer");
  });

  it("counts what has been answered", () => {
    const { ui } = setup();
    expect(ui.getByText(/0 of 2 answered/)).toBeTruthy();
    fireEvent.click(ui.getByLabelText(/Queue/));
    expect(ui.getByText(/1 of 2 answered/)).toBeTruthy();
  });

  it("caps a written answer at the server's limit", () => {
    const { container } = setup();
    expect(container.querySelector("textarea")!.maxLength).toBe(4000);
  });

  it("shows a tag when one is supplied", () => {
    const { container } = render(
      <AnswerSheet sheet={SHEET} submitting={false} onSubmit={vi.fn()}
        tagFor={(i) => (i.slot_id === "A-01" ? "re-test: same question" : null)} />,
    );
    expect(within(container).getAllByText("re-test: same question")).toHaveLength(1);
  });
});
