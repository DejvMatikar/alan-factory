import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  disclosureSummary,
  expectDisclosureState,
  expectTogglesOnEveryActivation,
} from "../test/disclosure";
import MarkdownPanel from "./MarkdownPanel";

const MARKDOWN = "# Heading\n\nSome body text.";

function getSummary(title: string): HTMLElement {
  const summary = screen.getByText(title).closest("summary");
  if (summary === null) throw new Error("summary element not found");
  return summary;
}

describe("MarkdownPanel", () => {
  it("collapses the content when an open disclosure is toggled closed", () => {
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const content = screen.getByTestId("notes-panel");
    expect(content).toBeVisible();

    fireEvent.click(screen.getByText("Notes"));

    expect(content).not.toBeVisible();
  });

  it("expands the content when a collapsed disclosure is toggled open", () => {
    render(
      <MarkdownPanel
        title="Notes"
        ariaLabel="Notes content"
        testId="notes-panel"
        defaultOpen={false}
      >
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const content = screen.getByTestId("notes-panel");
    expect(content).not.toBeVisible();

    fireEvent.click(screen.getByText("Notes"));

    expect(content).toBeVisible();
  });

  it("switches between formatted and source views via the shared ghost toggle", async () => {
    const user = userEvent.setup();
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    expect(screen.getByRole("heading", { level: 4, name: "Heading" })).toBeInTheDocument();
    const toggle = screen.getByTestId("notes-panel-source-toggle");
    expect(toggle).toHaveTextContent("View source");

    await user.click(toggle);

    expect(toggle).toHaveTextContent("View formatted");
    expect(screen.queryByRole("heading", { level: 4, name: "Heading" })).not.toBeInTheDocument();
    expect(screen.getByText(/# Heading/)).toBeInTheDocument();

    await user.click(toggle);

    expect(toggle).toHaveTextContent("View source");
    expect(screen.getByRole("heading", { level: 4, name: "Heading" })).toBeInTheDocument();
  });

  it("opens and closes the disclosure by click, Enter and Space, and ignores other keys", async () => {
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const disclosure = screen.getByTestId("notes-panel-disclosure");
    await expectDisclosureState(disclosure, true);
    await expectTogglesOnEveryActivation(disclosure);
  });

  it("shows the closed marker when collapsed by default and keeps the title beside it", async () => {
    render(
      <MarkdownPanel
        title="Notes"
        ariaLabel="Notes content"
        testId="notes-panel"
        defaultOpen={false}
      >
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const disclosure = screen.getByTestId("notes-panel-disclosure");
    await expectDisclosureState(disclosure, false);
    expect(getSummary("Notes")).toBe(disclosureSummary(disclosure));
  });

  it("passes its className to the disclosure element", () => {
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel" className="mt-4">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    expect(screen.getByTestId("notes-panel-disclosure")).toHaveClass("mt-4");
  });

  it("toggles the source/formatted view via keyboard activation on the ghost button", async () => {
    const user = userEvent.setup();
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const toggle = screen.getByTestId("notes-panel-source-toggle");
    toggle.focus();

    await user.keyboard("{Enter}");

    expect(toggle).toHaveTextContent("View formatted");
  });

  it("exposes the toggle's pressed state to assistive technology via aria-pressed", async () => {
    const user = userEvent.setup();
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const toggle = screen.getByTestId("notes-panel-source-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  it("applies no max-height or vertical-overflow class to its own content wrapper", () => {
    render(
      <MarkdownPanel title="Notes" ariaLabel="Notes content" testId="notes-panel">
        {MARKDOWN}
      </MarkdownPanel>,
    );
    const content = screen.getByTestId("notes-panel");
    expect(content.className).not.toMatch(/max-h-/);
    expect(content.className).not.toMatch(/overflow-y/);
  });
});
