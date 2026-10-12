import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { writeProjectFeaturesOn } from "../projectFeatures";
import { useProjectFeatures } from "./useProjectFeatures";

describe("useProjectFeatures", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("is false (off) when nothing is stored", () => {
    const { result } = renderHook(() => useProjectFeatures());
    expect(result.current).toBe(false);
  });

  it("is true when already turned on at mount", () => {
    writeProjectFeaturesOn(true);
    const { result } = renderHook(() => useProjectFeatures());
    expect(result.current).toBe(true);
  });

  it("flips to true when turned on in the same tab, with no remount", () => {
    const { result } = renderHook(() => useProjectFeatures());
    expect(result.current).toBe(false);
    act(() => {
      writeProjectFeaturesOn(true);
    });
    expect(result.current).toBe(true);
  });

  it("flips back to false when turned off, with no remount", () => {
    writeProjectFeaturesOn(true);
    const { result } = renderHook(() => useProjectFeatures());
    expect(result.current).toBe(true);
    act(() => {
      writeProjectFeaturesOn(false);
    });
    expect(result.current).toBe(false);
  });
});
