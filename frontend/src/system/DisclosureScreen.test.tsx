import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { DisclosureScreen } from "./DisclosureScreen";

afterEach(cleanup);

describe("DisclosureScreen", () => {
  it("says that written answers go to the model, and that its marks can be contested", () => {
    const { container } = render(<DisclosureScreen onAccepted={vi.fn()} onBack={vi.fn()} />);
    expect(container.textContent).toMatch(/written answers are sent to the same model/i);
    expect(container.textContent).toMatch(/contest any mark/i);
  });

  it("counts its own points correctly", () => {
    const { container } = render(<DisclosureScreen onAccepted={vi.fn()} onBack={vi.fn()} />);
    const points = container.querySelectorAll(".disc__points > li").length;
    expect(points).toBe(4);
    // Three points about papers and one about marking: the lead has to say both,
    // or it promises three things above a list of four.
    const lead = container.querySelector(".disc__lead")!.textContent!.replace(/\s+/g, " ");
    expect(lead).toMatch(/Three things are true of every paper it makes, and one of every mark it gives\./);
  });
});
