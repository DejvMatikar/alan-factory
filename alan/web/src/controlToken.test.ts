import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CONTROL_TOKEN_STORAGE_KEY,
  clearControlToken,
  readControlToken,
  subscribeControlToken,
  writeControlToken,
} from "./controlToken";

describe("controlToken", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("reads an empty string when nothing is stored", () => {
    expect(readControlToken()).toBe("");
  });

  it("round-trips a written value", () => {
    writeControlToken("secret-token");
    expect(readControlToken()).toBe("secret-token");
  });

  it("clear removes the stored key rather than writing an empty string", () => {
    writeControlToken("secret-token");
    clearControlToken();
    expect(localStorage.getItem(CONTROL_TOKEN_STORAGE_KEY)).toBeNull();
    expect(readControlToken()).toBe("");
  });

  it("writing an empty string removes the key instead of storing it", () => {
    writeControlToken("secret-token");
    writeControlToken("");
    expect(localStorage.getItem(CONTROL_TOKEN_STORAGE_KEY)).toBeNull();
    expect(readControlToken()).toBe("");
  });

  it("writing a whitespace-only string removes the key", () => {
    writeControlToken("secret-token");
    writeControlToken("   ");
    expect(localStorage.getItem(CONTROL_TOKEN_STORAGE_KEY)).toBeNull();
    expect(readControlToken()).toBe("");
  });

  it("trims accidental surrounding whitespace when writing", () => {
    writeControlToken("  secret-token  ");
    expect(readControlToken()).toBe("secret-token");
  });

  it("notifies subscribers on write", () => {
    const listener = vi.fn();
    subscribeControlToken(listener);
    writeControlToken("secret-token");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("notifies subscribers on clear", () => {
    writeControlToken("secret-token");
    const listener = vi.fn();
    subscribeControlToken(listener);
    clearControlToken();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stops delivering notifications once unsubscribed", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeControlToken(listener);
    unsubscribe();
    writeControlToken("secret-token");
    expect(listener).not.toHaveBeenCalled();
  });

  it("notifies every subscriber, not just the first", () => {
    const first = vi.fn();
    const second = vi.fn();
    subscribeControlToken(first);
    subscribeControlToken(second);
    writeControlToken("secret-token");
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});
