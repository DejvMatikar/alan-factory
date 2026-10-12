import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { clearControlToken, writeControlToken } from "../controlToken";
import { useControlTokenPresent } from "./useControlTokenPresent";

describe("useControlTokenPresent", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("is false when no token is stored", () => {
    const { result } = renderHook(() => useControlTokenPresent());
    expect(result.current).toBe(false);
  });

  it("is true when a token is already stored at mount", () => {
    writeControlToken("secret-token");
    const { result } = renderHook(() => useControlTokenPresent());
    expect(result.current).toBe(true);
  });

  it("flips to true when a token is written in the same tab, with no remount", () => {
    const { result } = renderHook(() => useControlTokenPresent());
    expect(result.current).toBe(false);
    act(() => {
      writeControlToken("secret-token");
    });
    expect(result.current).toBe(true);
  });

  it("flips back to false when the token is cleared, with no remount", () => {
    writeControlToken("secret-token");
    const { result } = renderHook(() => useControlTokenPresent());
    expect(result.current).toBe(true);
    act(() => {
      clearControlToken();
    });
    expect(result.current).toBe(false);
  });
});
