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
});
