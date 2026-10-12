import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import MarkdownBody from "./MarkdownBody";

describe("MarkdownBody", () => {
  it("renders plain text as a paragraph", () => {
    render(<MarkdownBody>{"plain text"}</MarkdownBody>);
    expect(screen.getByText("plain text")).toBeInTheDocument();
  });

  it("applies optional className to the wrapper div", () => {
    const { container } = render(
      <MarkdownBody className="text-sm text-slate-700">{"text"}</MarkdownBody>,
    );
    expect(container.firstChild).toHaveClass("text-sm", "text-slate-700");
  });

  it("defaults to the prose measure, capping width at max-w-prose", () => {
    const { container } = render(<MarkdownBody>{"text"}</MarkdownBody>);
    expect(container.firstChild).toHaveClass("max-w-prose");
    expect(container.firstChild).not.toHaveClass("w-full");
  });

  it("fills its container at the full measure", () => {
    const { container } = render(<MarkdownBody measure="full">{"text"}</MarkdownBody>);
    expect(container.firstChild).toHaveClass("w-full");
    expect(container.firstChild).not.toHaveClass("max-w-prose");
  });

  it("combines the full measure with an explicit className", () => {
    const { container } = render(
      <MarkdownBody measure="full" className="text-sm">
        {"text"}
      </MarkdownBody>,
    );
    expect(container.firstChild).toHaveClass("w-full", "text-sm");
  });

  it("renders markdown #/##/### shifted to h4/h5/h6 so they do not collide with page-level h2", () => {
    render(<MarkdownBody>{"# One\n## Two\n### Three"}</MarkdownBody>);
    expect(
      screen.getByRole("heading", { level: 4, name: "One" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 5, name: "Two" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 6, name: "Three" }),
    ).toBeInTheDocument();
  });

  it("renders bold text inside a strong element", () => {
    render(<MarkdownBody>{"**bold text**"}</MarkdownBody>);
    expect(screen.getByText("bold text").tagName).toBe("STRONG");
  });

  it("renders an unordered list with bullet items", () => {
    render(<MarkdownBody>{"- alpha\n- beta"}</MarkdownBody>);
    const list = screen.getByRole("list");
    expect(list.tagName).toBe("UL");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders an ordered list", () => {
    render(<MarkdownBody>{"1. first\n2. second"}</MarkdownBody>);
    const list = screen.getByRole("list");
    expect(list.tagName).toBe("OL");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders a GFM pipe table with columnheaders inside an overflow-x-auto wrapper", () => {
    const { container } = render(
      <MarkdownBody>{"| A | B |\n| --- | --- |\n| 1 | 2 |"}</MarkdownBody>,
    );
    const table = screen.getByRole("table");
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(container.querySelector(".overflow-x-auto table")).toBe(table);
  });

  it("renders fenced code as a bordered overflow-x-auto pre element", () => {
    const { container } = render(<MarkdownBody>{"```\nconst x = 1;\n```"}</MarkdownBody>);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre).toHaveClass("overflow-x-auto");
  });

  it("renders inline code with the chip class", () => {
    render(<MarkdownBody>{"use `inline code` here"}</MarkdownBody>);
    const code = screen.getByText("inline code");
    expect(code.tagName).toBe("CODE");
    expect(code).toHaveClass("bg-surface", "px-1", "py-0.5", "font-mono", "text-xs");
  });

  it("renders a link with href, target=_blank and rel=noopener noreferrer", () => {
    render(<MarkdownBody>{"[click here](https://example.com)"}</MarkdownBody>);
    const link = screen.getByRole("link", { name: "click here" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders a blockquote", () => {
    const { container } = render(<MarkdownBody>{"> quoted text"}</MarkdownBody>);
    expect(container.querySelector("blockquote")).toHaveTextContent("quoted text");
  });

  it("renders a horizontal rule", () => {
    const { container } = render(<MarkdownBody>{"above\n\n---\n\nbelow"}</MarkdownBody>);
    expect(container.querySelector("hr")).not.toBeNull();
  });

  it("renders markdown ####/#####/###### all as level-6 headings", () => {
    render(<MarkdownBody>{"#### Four\n\n##### Five\n\n###### Six"}</MarkdownBody>);
    expect(screen.getByRole("heading", { level: 6, name: "Four" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 6, name: "Five" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 6, name: "Six" })).toBeInTheDocument();
  });

  it("renders GFM strikethrough as a DEL element, proving remark-gfm is wired", () => {
    render(<MarkdownBody>{"~~gone~~"}</MarkdownBody>);
    expect(screen.getByText("gone").tagName).toBe("DEL");
  });
});
