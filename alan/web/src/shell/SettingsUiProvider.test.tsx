import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { SettingsUiContext, SettingsUiProvider, useSettingsUi } from "./SettingsUiProvider";

describe("SettingsUiProvider", () => {
  it("the default context value (no provider) is inert", () => {
    const { result } = renderHook(() => useSettingsUi());
    expect(result.current.isOpen).toBe(false);
    expect(result.current.closedCount).toBe(0);
    expect(() => {
      result.current.open();
    }).not.toThrow();
    expect(() => {
      result.current.close();
    }).not.toThrow();
  });

  it("open() flips isOpen to true", () => {
    const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
      <SettingsUiProvider>{children}</SettingsUiProvider>
    );
    const { result } = renderHook(() => useSettingsUi(), { wrapper });
    expect(result.current.isOpen).toBe(false);
    act(() => {
      result.current.open();
    });
    expect(result.current.isOpen).toBe(true);
  });

  it("close() flips isOpen back to false and increments closedCount", () => {
    const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
      <SettingsUiProvider>{children}</SettingsUiProvider>
    );
    const { result } = renderHook(() => useSettingsUi(), { wrapper });
    act(() => {
      result.current.open();
    });
    act(() => {
      result.current.close();
    });
    expect(result.current.isOpen).toBe(false);
    expect(result.current.closedCount).toBe(1);
  });

  it("closedCount increments once per close, not per open", () => {
    const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
      <SettingsUiProvider>{children}</SettingsUiProvider>
    );
    const { result } = renderHook(() => useSettingsUi(), { wrapper });
    act(() => {
      result.current.open();
      result.current.close();
      result.current.open();
      result.current.close();
    });
    expect(result.current.closedCount).toBe(2);
  });

  it("exports SettingsUiContext so tests can inject a fixed value", () => {
    const fixed = { isOpen: true, open: () => undefined, close: () => undefined, closedCount: 7 };
    const { result } = renderHook(() => useSettingsUi(), {
      wrapper: ({ children }: Readonly<{ children: ReactNode }>) => (
        <SettingsUiContext.Provider value={fixed}>{children}</SettingsUiContext.Provider>
      ),
    });
    expect(result.current).toBe(fixed);
  });
});
