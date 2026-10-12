import { act, fireEvent, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isNearBottom, useStickToBottom } from "./useStickToBottom";

interface FakeGeometry {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

const ORIGINAL_SCROLLING_ELEMENT_DESCRIPTOR = Object.getOwnPropertyDescriptor(
  Document.prototype,
  "scrollingElement",
);

function stubScrollingElement(geometry: FakeGeometry | null): void {
  Object.defineProperty(document, "scrollingElement", {
    configurable: true,
    get: () => geometry as unknown as Element | null,
  });
}

function restoreScrollingElement(): void {
  if (ORIGINAL_SCROLLING_ELEMENT_DESCRIPTOR === undefined) return;
  Object.defineProperty(document, "scrollingElement", ORIGINAL_SCROLLING_ELEMENT_DESCRIPTOR);
}

class CapturedResizeObserver {
  static instances: CapturedResizeObserver[] = [];
  readonly callback: ResizeObserverCallback;
  disconnected = false;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    CapturedResizeObserver.instances.push(this);
  }
  observe = (): void => undefined;
  unobserve = (): void => undefined;
  disconnect(): void {
    this.disconnected = true;
  }
  fire(): void {
    this.callback([], this);
  }
}

function TestTarget({ tick }: Readonly<{ tick: number }>) {
  const { anchorRef, pinned, jumpToLatest } = useStickToBottom([tick]);
  return createElement(
    "div",
    {},
    createElement("div", { key: "anchor", ref: anchorRef, "data-testid": "anchor" }),
    createElement(
      "div",
      { key: "pinned", "data-testid": "pinned" },
      pinned ? "pinned" : "unpinned",
    ),
    createElement("button", { key: "jump", "data-testid": "jump", onClick: jumpToLatest }),
  );
}

describe("isNearBottom", () => {
  it("is true within the default threshold", () => {
    expect(isNearBottom({ scrollTop: 952, scrollHeight: 1000, clientHeight: 40 })).toBe(true);
  });

  it("is false beyond the default threshold", () => {
    expect(isNearBottom({ scrollTop: 800, scrollHeight: 1000, clientHeight: 40 })).toBe(false);
  });

  it("honors a custom threshold", () => {
    expect(isNearBottom({ scrollTop: 900, scrollHeight: 1000, clientHeight: 40 }, 100)).toBe(
      true,
    );
  });
});

describe("useStickToBottom", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    CapturedResizeObserver.instances = [];
    globalThis.ResizeObserver = CapturedResizeObserver;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    restoreScrollingElement();
    globalThis.ResizeObserver = originalResizeObserver;
  });

  it("starts pinned and scrolls the document to the bottom on mount", () => {
    const geometry = { scrollTop: 0, scrollHeight: 100, clientHeight: 100 };
    stubScrollingElement(geometry);
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));
    expect(getByTestId("pinned")).toHaveTextContent("pinned");
    expect(geometry.scrollTop).toBe(100);
  });

  it("staying near the bottom keeps auto-scrolling as new deps land", () => {
    const geometry = { scrollTop: 0, scrollHeight: 100, clientHeight: 100 };
    stubScrollingElement(geometry);
    const { rerender } = render(createElement(TestTarget, { tick: 0 }));
    expect(geometry.scrollTop).toBe(100);

    geometry.scrollHeight = 260;
    rerender(createElement(TestTarget, { tick: 1 }));

    expect(geometry.scrollTop).toBe(260);
  });

  it("scrolling away from the bottom flips pinned to false and suppresses the next auto-scroll", () => {
    const geometry = { scrollTop: 0, scrollHeight: 100, clientHeight: 100 };
    stubScrollingElement(geometry);
    const { getByTestId, rerender } = render(createElement(TestTarget, { tick: 0 }));
    expect(geometry.scrollTop).toBe(100);

    geometry.scrollHeight = 2000;
    geometry.scrollTop = 0;
    fireEvent.scroll(window);
    expect(getByTestId("pinned")).toHaveTextContent("unpinned");

    rerender(createElement(TestTarget, { tick: 1 }));
    expect(geometry.scrollTop).toBe(0);
  });

  it("jumpToLatest scrolls to the document bottom and re-pins", () => {
    const geometry = { scrollTop: 0, scrollHeight: 100, clientHeight: 100 };
    stubScrollingElement(geometry);
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));

    geometry.scrollHeight = 2000;
    geometry.scrollTop = 0;
    fireEvent.scroll(window);
    expect(getByTestId("pinned")).toHaveTextContent("unpinned");

    fireEvent.click(getByTestId("jump"));

    expect(geometry.scrollTop).toBe(2000);
    expect(getByTestId("pinned")).toHaveTextContent("pinned");
  });

  it("removes the scroll listener on unmount", () => {
    stubScrollingElement({ scrollTop: 0, scrollHeight: 100, clientHeight: 100 });
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(createElement(TestTarget, { tick: 0 }));
    unmount();
    expect(removeSpy).toHaveBeenCalledWith("scroll", expect.any(Function));
  });

  it("treats a null scrollingElement as pinned", () => {
    stubScrollingElement(null);
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));
    fireEvent.scroll(window);
    expect(getByTestId("pinned")).toHaveTextContent("pinned");
  });

  it("is not near bottom at intermediate scrollTop values, via a scroll event", () => {
    stubScrollingElement({ scrollTop: 0, scrollHeight: 100, clientHeight: 100 });
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));

    stubScrollingElement({ scrollTop: 400, scrollHeight: 1000, clientHeight: 100 });
    fireEvent.scroll(window);

    expect(getByTestId("pinned")).toHaveTextContent("unpinned");
  });

  it("a resize firing with no scroll event flips pinned to false once content grows past the fold", () => {
    stubScrollingElement({ scrollTop: 0, scrollHeight: 100, clientHeight: 100 });
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));
    expect(getByTestId("pinned")).toHaveTextContent("pinned");

    stubScrollingElement({ scrollTop: 0, scrollHeight: 2000, clientHeight: 100 });
    act(() => {
      CapturedResizeObserver.instances[0]!.fire();
    });

    expect(getByTestId("pinned")).toHaveTextContent("unpinned");
  });

  it("a resize back toward the bottom does not silently re-pin", () => {
    stubScrollingElement({ scrollTop: 0, scrollHeight: 100, clientHeight: 100 });
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));

    stubScrollingElement({ scrollTop: 0, scrollHeight: 2000, clientHeight: 100 });
    fireEvent.scroll(window);
    expect(getByTestId("pinned")).toHaveTextContent("unpinned");

    stubScrollingElement({ scrollTop: 1960, scrollHeight: 2000, clientHeight: 100 });
    act(() => {
      CapturedResizeObserver.instances[0]!.fire();
    });

    expect(getByTestId("pinned")).toHaveTextContent("unpinned");
  });

  it("a resize with a null scrollingElement is a no-op, treated as pinned", () => {
    stubScrollingElement(null);
    const { getByTestId } = render(createElement(TestTarget, { tick: 0 }));

    act(() => {
      CapturedResizeObserver.instances[0]!.fire();
    });

    expect(getByTestId("pinned")).toHaveTextContent("pinned");
  });

  it("disconnects the ResizeObserver on unmount", () => {
    stubScrollingElement({ scrollTop: 0, scrollHeight: 100, clientHeight: 100 });
    const { unmount } = render(createElement(TestTarget, { tick: 0 }));
    const observer = CapturedResizeObserver.instances[0]!;
    unmount();
    expect(observer.disconnected).toBe(true);
  });
});
