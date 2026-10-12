import { describe, expect, it } from "vitest";

import { errorMessageOf, isErrorWithMessagePrefix } from "./errorMessage";

describe("errorMessageOf", () => {
  it("uses .message for Error instances", () => {
    expect(errorMessageOf(new Error("boom"))).toBe("boom");
  });

  it("stringifies non-Error values", () => {
    expect(errorMessageOf("oops")).toBe("oops");
  });
});

describe("isErrorWithMessagePrefix", () => {
  it("returns true when the Error's message starts with the prefix", () => {
    expect(isErrorWithMessagePrefix(new Error("409: conflict"), "409:")).toBe(true);
  });

  it("returns false when the Error's message doesn't start with the prefix", () => {
    expect(isErrorWithMessagePrefix(new Error("500: boom"), "409:")).toBe(false);
  });

  it("returns false for a non-Error value", () => {
    expect(isErrorWithMessagePrefix("409: conflict", "409:")).toBe(false);
  });
});
