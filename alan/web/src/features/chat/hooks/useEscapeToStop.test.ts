import { fireEvent, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useEscapeToStop } from "./useEscapeToStop";

describe("useEscapeToStop", () => {
  it("calls onStop when Escape is pressed while thinking", () => {
    const onStop = vi.fn();
    renderHook(() => useEscapeToStop(true, onStop));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("does not call onStop when Escape is pressed while not thinking", () => {
    const onStop = vi.fn();
    renderHook(() => useEscapeToStop(false, onStop));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).not.toHaveBeenCalled();
  });

  it("ignores other keys while thinking", () => {
    const onStop = vi.fn();
    renderHook(() => useEscapeToStop(true, onStop));
    fireEvent.keyDown(document, { key: "Enter" });
    expect(onStop).not.toHaveBeenCalled();
  });

  it("uses the latest thinking/onStop values without re-binding the listener", () => {
    const onStopFirst = vi.fn();
    const onStopSecond = vi.fn();
    const { rerender } = renderHook(
      ({ thinking, onStop }: { thinking: boolean; onStop: () => void }) =>
        useEscapeToStop(thinking, onStop),
      { initialProps: { thinking: false, onStop: onStopFirst } },
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStopFirst).not.toHaveBeenCalled();

    rerender({ thinking: true, onStop: onStopSecond });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStopFirst).not.toHaveBeenCalled();
    expect(onStopSecond).toHaveBeenCalledTimes(1);
  });

  it("removes the listener on unmount", () => {
    const onStop = vi.fn();
    const { unmount } = renderHook(() => useEscapeToStop(true, onStop));
    unmount();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onStop).not.toHaveBeenCalled();
  });
});
