import { describe, expect, it } from "vitest";

import {
  extractInnerEvent,
  parseToolActivityLabel,
  safeJsonParse,
} from "./chatSseUtils";

describe("safeJsonParse", () => {
  it("returns the parsed value for valid JSON", () => {
    expect(safeJsonParse('{"a":1}')).toEqual({ a: 1 });
  });

  it("returns null for malformed input", () => {
    expect(safeJsonParse("not-json")).toBeNull();
  });
});

describe("extractInnerEvent", () => {
  it("returns the parsed inner JSON when shape is correct", () => {
    const inner = JSON.stringify({ type: "assistant" });
    const outer = JSON.stringify({ event: inner });
    expect(extractInnerEvent(outer)).toEqual({ type: "assistant" });
  });

  it("returns null when outer is not JSON", () => {
    expect(extractInnerEvent("nope")).toBeNull();
  });

  it("returns null when outer is not an object", () => {
    expect(extractInnerEvent("42")).toBeNull();
  });

  it("returns null when inner field is not a string", () => {
    expect(extractInnerEvent(JSON.stringify({ event: 42 }))).toBeNull();
  });

  it("returns null when inner is not parseable", () => {
    expect(extractInnerEvent(JSON.stringify({ event: "x" }))).toBeNull();
  });

  it("returns null when inner is not an object", () => {
    expect(extractInnerEvent(JSON.stringify({ event: "42" }))).toBeNull();
  });
});

describe("parseToolActivityLabel", () => {
  it("returns the tool name on a tool_use block", () => {
    const inner = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Read" }] },
    });
    const outer = JSON.stringify({ event: inner });
    expect(parseToolActivityLabel(outer)).toBe("Read");
  });

  it("returns null when the outer cannot be parsed", () => {
    expect(parseToolActivityLabel("bogus")).toBeNull();
  });
});
