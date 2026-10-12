import { renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useSessionSwitchReset } from "./useSessionSwitchReset";

describe("useSessionSwitchReset", () => {
  it("does not fire when a session id is acquired from null (back-fill)", () => {
    const onSwitch = vi.fn();
    const { rerender } = renderHook<void, { sessionId: string | null }>(
      ({ sessionId }) => useSessionSwitchReset(sessionId, onSwitch),
      { initialProps: { sessionId: null } },
    );
    rerender({ sessionId: "id-1" });
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("does not fire when a session id is cleared to null", () => {
    const onSwitch = vi.fn();
    const { rerender } = renderHook<void, { sessionId: string | null }>(
      ({ sessionId }) => useSessionSwitchReset(sessionId, onSwitch),
      { initialProps: { sessionId: "id-1" } },
    );
    rerender({ sessionId: null });
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("fires once when switching from one real session to another", () => {
    const onSwitch = vi.fn();
    const { rerender } = renderHook<void, { sessionId: string | null }>(
      ({ sessionId }) => useSessionSwitchReset(sessionId, onSwitch),
      { initialProps: { sessionId: "id-1" } },
    );
    rerender({ sessionId: "id-2" });
    expect(onSwitch).toHaveBeenCalledTimes(1);
  });

  it("does not fire when the same session id is re-set", () => {
    const onSwitch1 = vi.fn();
    const onSwitch2 = vi.fn();
    const { rerender } = renderHook<void, { sessionId: string | null; onSwitch: () => void }>(
      ({ sessionId, onSwitch }) => useSessionSwitchReset(sessionId, onSwitch),
      { initialProps: { sessionId: "id-1", onSwitch: onSwitch1 } },
    );
    rerender({ sessionId: "id-1", onSwitch: onSwitch2 });
    expect(onSwitch1).not.toHaveBeenCalled();
    expect(onSwitch2).not.toHaveBeenCalled();
  });

  it("does not fire on initial mount with a non-null session id", () => {
    const onSwitch = vi.fn();
    renderHook(() => useSessionSwitchReset("id-1", onSwitch));
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("does not double-fire under React.StrictMode", () => {
    const onSwitch = vi.fn();
    const { rerender } = renderHook<void, { sessionId: string | null }>(
      ({ sessionId }) => useSessionSwitchReset(sessionId, onSwitch),
      {
        initialProps: { sessionId: "id-1" },
        wrapper: StrictMode,
      },
    );
    rerender({ sessionId: "id-2" });
    expect(onSwitch).toHaveBeenCalledTimes(1);
  });
});
