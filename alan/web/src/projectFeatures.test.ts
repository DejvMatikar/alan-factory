import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PROJECT_FEATURES_STORAGE_KEY,
  readProjectFeaturesOn,
  subscribeProjectFeatures,
  writeProjectFeaturesOn,
} from "./projectFeatures";

describe("projectFeatures", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("reads off when nothing is stored", () => {
    expect(readProjectFeaturesOn()).toBe(false);
  });

  it("reads off for a garbage stored value", () => {
    localStorage.setItem(PROJECT_FEATURES_STORAGE_KEY, "yes");
    expect(readProjectFeaturesOn()).toBe(false);
  });

  it("round-trips writing on", () => {
    writeProjectFeaturesOn(true);
    expect(readProjectFeaturesOn()).toBe(true);
    expect(localStorage.getItem(PROJECT_FEATURES_STORAGE_KEY)).toBe("on");
  });

  it("writing off removes the key instead of storing a falsy value", () => {
    writeProjectFeaturesOn(true);
    writeProjectFeaturesOn(false);
    expect(localStorage.getItem(PROJECT_FEATURES_STORAGE_KEY)).toBeNull();
    expect(readProjectFeaturesOn()).toBe(false);
  });

  it("notifies subscribers on write", () => {
    const listener = vi.fn();
    subscribeProjectFeatures(listener);
    writeProjectFeaturesOn(true);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("notifies subscribers when writing off", () => {
    writeProjectFeaturesOn(true);
    const listener = vi.fn();
    subscribeProjectFeatures(listener);
    writeProjectFeaturesOn(false);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stops delivering notifications once unsubscribed", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeProjectFeatures(listener);
    unsubscribe();
    writeProjectFeaturesOn(true);
    expect(listener).not.toHaveBeenCalled();
  });

  it("notifies every subscriber, not just the first", () => {
    const first = vi.fn();
    const second = vi.fn();
    subscribeProjectFeatures(first);
    subscribeProjectFeatures(second);
    writeProjectFeaturesOn(true);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});
