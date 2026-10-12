import { render } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { useAutoScroll } from "./useAutoScroll";

function TestTarget({ tick, block }: Readonly<{ tick: number; block?: ScrollLogicalPosition }>) {
  const ref = useAutoScroll([tick], block);
  return createElement("div", { ref, "data-testid": "scroll-target" });
}

describe("useAutoScroll", () => {
  it("scrolls the referenced element into view on mount", () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    scrollIntoView.mockClear();

    render(createElement(TestTarget, { tick: 0 }));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" });
  });

  it("scrolls again whenever the deps change", () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const { rerender } = render(createElement(TestTarget, { tick: 0 }));
    scrollIntoView.mockClear();

    rerender(createElement(TestTarget, { tick: 1 }));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" });
  });

  it("does not scroll again when the deps are unchanged", () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const { rerender } = render(createElement(TestTarget, { tick: 0 }));
    scrollIntoView.mockClear();

    rerender(createElement(TestTarget, { tick: 0 }));

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("passes an explicit block argument through to scrollIntoView", () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    scrollIntoView.mockClear();

    render(createElement(TestTarget, { tick: 0, block: "nearest" }));

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("keeps block end when the argument is omitted", () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    scrollIntoView.mockClear();

    render(createElement(TestTarget, { tick: 0 }));

    expect(scrollIntoView).toHaveBeenCalledExactlyOnceWith({ block: "end" });
  });
});
