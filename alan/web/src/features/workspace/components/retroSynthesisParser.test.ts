import { describe, expect, it } from "vitest";

import { parseMarkdownSections } from "./retroSynthesisParser";

describe("parseMarkdownSections", () => {
  it("returns {} for empty input", () => {
    expect(parseMarkdownSections("")).toEqual({});
  });

  it("returns one key with trimmed body for a single section", () => {
    const result = parseMarkdownSections("## Foo\n  body line  \n  ");
    expect(result).toEqual({ Foo: "body line" });
  });

  it("returns three keys with correct bodies for three sections", () => {
    const input = "## A\nfirst\n## B\nsecond\n## C\nthird";
    expect(parseMarkdownSections(input)).toEqual({
      A: "first",
      B: "second",
      C: "third",
    });
  });

  it("trims bodies and discards blank-body sections", () => {
    const input = "## Empty\n\n## Real\n  content  \n";
    const result = parseMarkdownSections(input);
    expect("Empty" in result).toBe(false);
    expect(result.Real).toBe("content");
  });

  it("does not treat mid-line ## as a heading", () => {
    const input = "preamble ## not a heading\n## Actual\nbody";
    const result = parseMarkdownSections(input);
    expect(Object.keys(result)).toEqual(["Actual"]);
    expect(result.Actual).toBe("body");
  });

  it("does not split on ### subheadings, they are part of the body", () => {
    const input = "## Foo\nline one\n### sub\nmore";
    expect(parseMarkdownSections(input)).toEqual({
      Foo: "line one\n### sub\nmore",
    });
  });

  it("heading at end of text with no trailing newline yields no entry", () => {
    const result = parseMarkdownSections("## Real\ncontent\n## NoBody");
    expect(result).toEqual({ Real: "content" });
  });

  it("curly apostrophe U+2019 produces a distinct key from ASCII U+0027", () => {
    const CURLY = String.fromCharCode(0x2019);
    const curlySynthesis = `## What didn${CURLY}t\ncontent`;
    const result = parseMarkdownSections(curlySynthesis);
    expect("What didn't" in result).toBe(false);
    expect(`What didn${CURLY}t` in result).toBe(true);
    expect(result[`What didn${CURLY}t`]).toBe("content");
  });
});
