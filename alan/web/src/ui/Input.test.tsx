import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Input from "./Input";

interface ChromeCase {
  label: string;
  className?: string;
  tapTarget?: boolean;
  focusRing?: boolean;
  disabledStyle?: boolean;
  placeholderStyle?: boolean;
  expected: string;
}

const CHROME_CASES: readonly ChromeCase[] = [
  {
    label: "bare disabled-only",
    expected:
      "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary disabled:opacity-50",
  },
  {
    label: "placeholder plus disabled",
    placeholderStyle: true,
    expected:
      "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary placeholder:text-text-muted disabled:opacity-50",
  },
  {
    label: "no disabled appearance",
    disabledStyle: false,
    expected: "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary",
  },
  {
    label: "layout prefix plus placeholder, no disabled appearance",
    className: "mt-1",
    placeholderStyle: true,
    disabledStyle: false,
    expected:
      "mt-1 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary placeholder:text-text-muted",
  },
  {
    label: "tap target plus focus ring plus disabled",
    tapTarget: true,
    focusRing: true,
    expected:
      "min-h-11 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50",
  },
  {
    label: "tap target plus placeholder plus disabled",
    tapTarget: true,
    placeholderStyle: true,
    expected:
      "min-h-11 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary placeholder:text-text-muted disabled:opacity-50",
  },
  {
    label: "tap target plus disabled, no placeholder",
    tapTarget: true,
    expected:
      "min-h-11 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary disabled:opacity-50",
  },
];

describe("Input", () => {
  it.each(CHROME_CASES)("emits the byte-identical class string for: $label", (chromeCase) => {
    render(
      <Input
        as="input"
        data-testid="field"
        className={chromeCase.className}
        tapTarget={chromeCase.tapTarget}
        focusRing={chromeCase.focusRing}
        disabledStyle={chromeCase.disabledStyle}
        placeholderStyle={chromeCase.placeholderStyle}
      />,
    );
    expect(screen.getByTestId("field").className).toBe(chromeCase.expected);
  });

  it("defaults to no tap target, no focus ring, and no placeholder styling", () => {
    render(<Input as="input" data-testid="field" />);
    const el = screen.getByTestId("field");
    expect(el.className).not.toContain("min-h-11");
    expect(el.className).not.toContain("focus-visible");
    expect(el.className).not.toContain("placeholder:");
    expect(el.className).toContain("disabled:opacity-50");
  });

  it("renders as an <input> by default among element kinds", () => {
    render(<Input as="input" data-testid="field" />);
    expect(screen.getByTestId("field").tagName).toBe("INPUT");
  });

  it("renders as a <textarea>", () => {
    render(<Input as="textarea" data-testid="field" />);
    expect(screen.getByTestId("field").tagName).toBe("TEXTAREA");
  });

  it("renders as a <select> with its option children", () => {
    render(
      <Input as="select" data-testid="field">
        <option value="a">A</option>
        <option value="b">B</option>
      </Input>,
    );
    const el = screen.getByTestId("field");
    expect(el.tagName).toBe("SELECT");
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });

  it("passes native input attributes through untouched", () => {
    const handleChange = vi.fn();
    render(
      <Input as="input" type="email" data-testid="field" value="v" onChange={handleChange} />,
    );
    const el: HTMLInputElement = screen.getByTestId("field");
    expect(el.type).toBe("email");
    expect(el.value).toBe("v");
  });

  it("passes native textarea attributes through untouched", () => {
    const handleChange = vi.fn();
    render(
      <Input as="textarea" rows={5} data-testid="field" value="v" onChange={handleChange} />,
    );
    const el: HTMLTextAreaElement = screen.getByTestId("field");
    expect(el.rows).toBe(5);
    expect(el.value).toBe("v");
  });

  it("rejects placeholderStyle on a select at compile time", () => {
    const invalidSelectPlaceholder: unknown = (
      // @ts-expect-error placeholderStyle is never on the select branch
      <Input as="select" placeholderStyle data-testid="x"><option value="a">a</option></Input>
    );
    expect(invalidSelectPlaceholder).toBeDefined();
  });

  it("rejects children on an input at compile time", () => {
    const invalidInputChildren: unknown = (
      // @ts-expect-error children is never on the input branch
      <Input as="input" data-testid="x">child</Input>
    );
    expect(invalidInputChildren).toBeDefined();
  });

  it("rejects children on a textarea at compile time", () => {
    const invalidTextareaChildren: unknown = (
      // @ts-expect-error children is never on the textarea branch
      <Input as="textarea" data-testid="x">child</Input>
    );
    expect(invalidTextareaChildren).toBeDefined();
  });
});
