import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { placeholderSegment, textSegment } from "../test/agentFixtures";
import AgentPromptViewer from "./AgentPromptViewer";

const SEGMENTS = [
  textSegment("# Role\nWrite code. **Write** tests.\n"),
  textSegment("Be terse. Write less.\n", "terseness_block", true),
  placeholderSegment("TASK_TITLE", "The title of the task"),
];

function renderViewer(term?: string, segments = SEGMENTS) {
  return render(
    <AgentPromptViewer segments={segments} title="System prompt" testId="viewer" {...(term === undefined ? {} : { term })} />,
  );
}

describe("AgentPromptViewer — plain read-only text", () => {
  it("renders the prompt inside a pre as literal text, never as markdown", () => {
    renderViewer();
    const pre = screen.getByTestId("viewer-text");
    expect(pre.tagName).toBe("PRE");
    expect(pre).toHaveTextContent("# Role");
    expect(pre).toHaveTextContent("**Write** tests.");
    expect(pre.querySelector("h1, strong, em")).toBeNull();
  });

  it("names the region after its title", () => {
    renderViewer();
    expect(screen.getByRole("region", { name: "System prompt" })).toBeInTheDocument();
  });

  it("shows a placeholder as its brace token and describes it in the legend", () => {
    renderViewer();
    expect(screen.getByTestId("viewer-text")).toHaveTextContent("{{TASK_TITLE}}");
    expect(screen.getByTestId("viewer-legend")).toHaveTextContent("{{TASK_TITLE}} — The title of the task");
  });

  it("falls back to a generic description for a placeholder without one", () => {
    renderViewer("", [{ kind: "placeholder", label: "NONCE", text: "", description: null, shared: false }]);
    expect(screen.getByTestId("viewer-legend")).toHaveTextContent("{{NONCE}} — Filled in with a run-time value.");
  });

  it("labels a shared block in the text and in the legend", () => {
    renderViewer();
    expect(screen.getByTestId("viewer-shared-label")).toHaveTextContent("[shared block: terseness_block]");
    expect(screen.getByTestId("viewer-legend")).toHaveTextContent("terseness_block — Shared prompt block");
  });

  it("omits the legend and shared label when the prompt has neither", () => {
    renderViewer("", [textSegment("plain text only")]);
    expect(screen.queryByTestId("viewer-legend")).not.toBeInTheDocument();
    expect(screen.queryByTestId("viewer-shared-label")).not.toBeInTheDocument();
  });

  it("lists a repeated placeholder once in the legend", () => {
    renderViewer("", [placeholderSegment("A", "first"), textSegment("x"), placeholderSegment("A", "first")]);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("renders an empty prompt as an empty pre", () => {
    renderViewer("", []);
    expect(screen.getByTestId("viewer-text")).toBeEmptyDOMElement();
  });
});

describe("AgentPromptViewer — match highlighting and navigation", () => {
  it("shows no match controls without a term, only the copy action", () => {
    renderViewer();
    expect(screen.queryByTestId("viewer-status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("viewer-next")).not.toBeInTheDocument();
    expect(screen.getByTestId("viewer-copy")).toBeInTheDocument();
  });

  it("treats a whitespace-only term as no term", () => {
    renderViewer("   ");
    expect(screen.queryByTestId("viewer-status")).not.toBeInTheDocument();
    expect(screen.queryAllByTestId("viewer-match")).toHaveLength(0);
  });

  it("highlights every occurrence case-insensitively and announces the first as current", () => {
    renderViewer("write");
    const marks = screen.getAllByTestId("viewer-match");
    expect(marks).toHaveLength(3);
    expect(marks.map((mark) => mark.tagName)).toEqual(["MARK", "MARK", "MARK"]);
    expect(marks[0]).toHaveAttribute("data-current", "true");
    expect(marks[1]).not.toHaveAttribute("data-current");
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 1 of 3");
    expect(screen.getByTestId("viewer-status")).toHaveAttribute("aria-live", "polite");
  });

  it("draws a match split across two segments as two marks but anchors the current match once", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    const user = userEvent.setup();
    renderViewer("ab", [textSegment("xa"), textSegment("by ab", "tail")]);
    const marks = screen.getAllByTestId("viewer-match");
    expect(marks.map((mark) => mark.textContent)).toEqual(["a", "b", "ab"]);
    expect(marks.map((mark) => mark.hasAttribute("data-current"))).toEqual([true, false, false]);
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 1 of 2");
    await user.click(screen.getByTestId("viewer-next"));
    await user.click(screen.getByTestId("viewer-prev"));
    expect(scroll).toHaveBeenCalledTimes(2);
  });

  it("highlights a match inside a placeholder token", () => {
    renderViewer("task_title");
    expect(screen.getByTestId("viewer-match")).toHaveTextContent("TASK_TITLE");
  });

  it("moves to the next and previous match, wrapping at both ends", async () => {
    const user = userEvent.setup();
    renderViewer("write");
    await user.click(screen.getByTestId("viewer-next"));
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 2 of 3");
    expect(screen.getAllByTestId("viewer-match")[1]).toHaveAttribute("data-current", "true");
    await user.click(screen.getByTestId("viewer-next"));
    await user.click(screen.getByTestId("viewer-next"));
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 1 of 3");
    await user.click(screen.getByTestId("viewer-prev"));
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 3 of 3");
  });

  it("scrolls the current match into view only after the reader navigates", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    const user = userEvent.setup();
    renderViewer("write");
    expect(scroll).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("viewer-next"));
    expect(scroll).toHaveBeenCalledWith({ block: "center" });
  });

  it("resets to the first match when the term changes", async () => {
    const user = userEvent.setup();
    const { rerender } = renderViewer("write");
    await user.click(screen.getByTestId("viewer-next"));
    rerender(<AgentPromptViewer segments={SEGMENTS} title="System prompt" testId="viewer" term="be" />);
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("Match 1 of 1");
  });

  it("reports no matches without offering navigation", () => {
    renderViewer("zzz");
    expect(screen.getByTestId("viewer-status")).toHaveTextContent("No matches for “zzz” in this prompt");
    expect(screen.queryByTestId("viewer-next")).not.toBeInTheDocument();
    expect(screen.queryByTestId("viewer-prev")).not.toBeInTheDocument();
  });
});

describe("AgentPromptViewer — copy", () => {
  it("copies the plain prompt text without the shared-block label and says so", async () => {
    const user = userEvent.setup();
    renderViewer();
    await user.click(screen.getByTestId("viewer-copy"));
    await waitFor(() => {
      expect(screen.getByTestId("viewer-copy-status")).toHaveTextContent("Copied to the clipboard");
    });
    const copied = await navigator.clipboard.readText();
    expect(copied).toBe("# Role\nWrite code. **Write** tests.\nBe terse. Write less.\n{{TASK_TITLE}}");
    expect(copied).not.toContain("[shared block");
  });

  it("reports a failed copy", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    renderViewer();
    await user.click(screen.getByTestId("viewer-copy"));
    await waitFor(() => {
      expect(screen.getByTestId("viewer-copy-status")).toHaveTextContent("Copy failed");
    });
  });
});
