import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import EditableBrief from "./EditableBrief";

describe("EditableBrief", () => {
  it("shows the empty hint when brief is null", () => {
    render(<EditableBrief brief={null} value="" onChange={vi.fn()} onEdit={vi.fn()} />);
    expect(screen.getByTestId("summary-empty")).toBeInTheDocument();
    expect(screen.getByTestId("summary-textarea")).toHaveValue("");
  });

  it("renders the given value and hides the empty hint when brief is present", () => {
    render(
      <EditableBrief brief={{ content: "hello" }} value="hello" onChange={vi.fn()} onEdit={vi.fn()} />,
    );
    expect(screen.queryByTestId("summary-empty")).not.toBeInTheDocument();
    expect(screen.getByTestId("summary-textarea")).toHaveValue("hello");
  });

  it("calls onEdit on blur when the value differs from the brief content", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(
      <EditableBrief brief={{ content: "orig" }} value="edited" onChange={vi.fn()} onEdit={onEdit} />,
    );
    await user.click(screen.getByTestId("summary-textarea"));
    await user.tab();
    expect(onEdit).toHaveBeenCalledWith("edited");
  });

  it("does not call onEdit on blur when the value matches the brief content", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(
      <EditableBrief brief={{ content: "same" }} value="same" onChange={vi.fn()} onEdit={onEdit} />,
    );
    await user.click(screen.getByTestId("summary-textarea"));
    await user.tab();
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("does not call onEdit on blur when brief is null", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(<EditableBrief brief={null} value="" onChange={vi.fn()} onEdit={onEdit} />);
    await user.click(screen.getByTestId("summary-textarea"));
    await user.tab();
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("applies an explicit rows value", () => {
    render(<EditableBrief brief={null} value="" onChange={vi.fn()} onEdit={vi.fn()} rows={12} />);
    expect(screen.getByTestId("summary-textarea")).toHaveAttribute("rows", "12");
  });

  it("defaults rows to 20", () => {
    render(<EditableBrief brief={null} value="" onChange={vi.fn()} onEdit={vi.fn()} />);
    expect(screen.getByTestId("summary-textarea")).toHaveAttribute("rows", "20");
  });

  it("calls onChange on every keystroke", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <EditableBrief brief={{ content: "init" }} value="init" onChange={onChange} onEdit={vi.fn()} />,
    );
    const textarea = screen.getByTestId("summary-textarea");
    await user.type(textarea, "x");
    expect(onChange).toHaveBeenCalledWith("initx");
  });

  it("defaults to edit mode, rendering the textarea", () => {
    render(<EditableBrief brief={{ content: "abc" }} value="abc" onChange={vi.fn()} onEdit={vi.fn()} />);
    expect(screen.getByTestId("summary-textarea")).toBeInTheDocument();
    expect(screen.queryByTestId("summary-preview")).not.toBeInTheDocument();
  });

  it("renders the formatted preview instead of the textarea in preview mode", () => {
    render(
      <EditableBrief
        brief={{ content: "# Heading" }}
        value="# Heading"
        onChange={vi.fn()}
        onEdit={vi.fn()}
        mode="preview"
      />,
    );
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
    const preview = screen.getByTestId("summary-preview");
    expect(preview).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 4, name: "Heading" })).toBeInTheDocument();
  });

  it("still shows the empty-state hint above the preview when brief is null", () => {
    render(
      <EditableBrief brief={null} value="" onChange={vi.fn()} onEdit={vi.fn()} mode="preview" />,
    );
    expect(screen.getByTestId("summary-empty")).toBeInTheDocument();
    expect(screen.getByTestId("summary-preview")).toBeInTheDocument();
  });

  it("labels the preview region so the formatted output has an accessible name", () => {
    render(
      <EditableBrief
        brief={{ content: "# Heading" }}
        value="# Heading"
        onChange={vi.fn()}
        onEdit={vi.fn()}
        mode="preview"
      />,
    );
    expect(screen.getByTestId("summary-content")).toHaveAttribute("aria-label", "Summary");
  });

  it("does not duplicate the accessible name on the wrapping section when the textarea already labels itself", () => {
    render(
      <EditableBrief brief={{ content: "abc" }} value="abc" onChange={vi.fn()} onEdit={vi.fn()} mode="edit" />,
    );
    expect(screen.getByTestId("summary-content")).not.toHaveAttribute("aria-label");
    expect(screen.getByTestId("summary-textarea")).toHaveAttribute("aria-label", "Summary");
  });

  it("focuses the textarea on mount when in edit mode with focusOnEdit", () => {
    render(
      <EditableBrief
        brief={{ content: "abc" }}
        value="abc"
        onChange={vi.fn()}
        onEdit={vi.fn()}
        mode="edit"
        focusOnEdit
      />,
    );
    expect(screen.getByTestId("summary-textarea")).toHaveFocus();
  });

  it("does not steal focus when focusOnEdit is omitted, even though the textarea mounts", () => {
    const { rerender } = render(
      <>
        <button data-testid="sentinel">sentinel</button>
      </>,
    );
    screen.getByTestId("sentinel").focus();
    expect(screen.getByTestId("sentinel")).toHaveFocus();

    rerender(
      <>
        <button data-testid="sentinel">sentinel</button>
        <EditableBrief brief={{ content: "abc" }} value="abc" onChange={vi.fn()} onEdit={vi.fn()} mode="edit" />
      </>,
    );

    expect(screen.getByTestId("sentinel")).toHaveFocus();
  });

  it("gives the edit textarea and the preview wrapper the same full-width measure, so toggling mode is width-stable", () => {
    const { rerender } = render(
      <EditableBrief brief={{ content: "abc" }} value="abc" onChange={vi.fn()} onEdit={vi.fn()} mode="edit" />,
    );
    expect(screen.getByTestId("summary-textarea").className).toContain("w-full");
    expect(screen.getByTestId("summary-textarea").className).not.toContain("max-w-prose");

    rerender(
      <EditableBrief brief={{ content: "abc" }} value="abc" onChange={vi.fn()} onEdit={vi.fn()} mode="preview" />,
    );
    expect(screen.getByTestId("summary-preview").className).toContain("w-full");
    expect(screen.getByTestId("summary-preview").className).not.toContain("max-w-prose");
  });

  it("is inert when mode is preview, since the textarea it would apply to isn't mounted", () => {
    render(
      <EditableBrief
        brief={{ content: "# Heading" }}
        value="# Heading"
        onChange={vi.fn()}
        onEdit={vi.fn()}
        mode="preview"
        focusOnEdit
      />,
    );
    expect(screen.queryByTestId("summary-textarea")).not.toBeInTheDocument();
  });
});
