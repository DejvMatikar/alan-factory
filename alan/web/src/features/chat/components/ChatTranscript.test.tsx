import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../../api";
import type { ToolActivity } from "../hooks/useChatSessionGeneric";
import ChatTranscript, { shouldRenderMarkdown } from "./ChatTranscript";

const HINT = "give me a hint";

type MessageFixture = Omit<ChatMessage, "interrupted"> & { interrupted?: boolean };

function transcript(props: {
  messages?: readonly MessageFixture[];
  toolActivity?: readonly ToolActivity[];
  thinking?: boolean;
  queuedMessage?: string | null;
  onCancelQueued?: () => void;
  scrollAnchorRef?: ReturnType<typeof createRef<HTMLDivElement>>;
}) {
  return (
    <ChatTranscript
      scrollAnchorRef={props.scrollAnchorRef ?? createRef<HTMLDivElement>()}
      messages={(props.messages ?? []).map((m) => ({ interrupted: false, ...m }))}
      toolActivity={props.toolActivity ?? []}
      thinking={props.thinking ?? false}
      emptyHint={HINT}
      {...(props.queuedMessage !== undefined ? { queuedMessage: props.queuedMessage } : {})}
      {...(props.onCancelQueued !== undefined ? { onCancelQueued: props.onCancelQueued } : {})}
    />
  );
}

describe("shouldRenderMarkdown", () => {
  it("is false for a user message regardless of content", () => {
    expect(shouldRenderMarkdown("user", "# Heading", false)).toBe(false);
  });

  it("is false for an assistant message with blank (or whitespace-only) content", () => {
    expect(shouldRenderMarkdown("assistant", "", false)).toBe(false);
    expect(shouldRenderMarkdown("assistant", "   ", false)).toBe(false);
  });

  it("is true for an assistant message with non-blank content", () => {
    expect(shouldRenderMarkdown("assistant", "# Heading", false)).toBe(true);
  });

  it("is false for an interrupted assistant message even with markdown-shaped partial content", () => {
    expect(shouldRenderMarkdown("assistant", "**bold", true)).toBe(false);
  });
});

describe("ChatTranscript", () => {
  it("shows the empty hint when there are no messages and not thinking", () => {
    render(transcript({}));
    expect(screen.getByTestId("chat-empty")).toHaveTextContent(HINT);
  });

  it("hides the empty hint when thinking", () => {
    render(transcript({ thinking: true }));
    expect(screen.queryByTestId("chat-empty")).toBeNull();
    expect(screen.getByTestId("thinking")).toBeInTheDocument();
  });

  it("renders user and assistant bubbles in order", () => {
    render(
      transcript({
        messages: [
          { role: "user", content: "u1", ts: "t1" },
          { role: "assistant", content: "a1", ts: "t2" },
        ],
      }),
    );
    expect(screen.getByTestId("chat-user")).toHaveTextContent("u1");
    expect(screen.getByTestId("chat-assistant")).toHaveTextContent("a1");
  });

  it("renders one collapsed activity line for in-flight tool activity", () => {
    render(
      transcript({
        toolActivity: [{ id: "tool-1", label: "Read foo.txt" }],
      }),
    );
    expect(screen.getByTestId("activity-line-current")).toHaveTextContent("Read foo.txt");
  });

  it("renders an activity line attached to a completed assistant message carrying operations", () => {
    render(
      transcript({
        messages: [
          {
            role: "assistant",
            content: "done",
            ts: "t1",
            operations: ["Read: a.py", "Bash: ls"],
          },
        ],
      }),
    );
    expect(screen.getByTestId("activity-line-current")).toHaveTextContent("Bash: ls");
  });

  it("renders no activity line for a completed assistant message with no operations", () => {
    render(
      transcript({
        messages: [{ role: "assistant", content: "done", ts: "t1" }],
      }),
    );
    expect(screen.queryByTestId("activity-line")).not.toBeInTheDocument();
  });

  it("renders no activity line attached to a user message even if it somehow carried operations", () => {
    render(
      transcript({
        messages: [{ role: "user", content: "hi", ts: "t1", operations: ["Read: a.py"] }],
      }),
    );
    expect(screen.queryByTestId("activity-line")).not.toBeInTheDocument();
  });

  it("renders both messages and the thinking indicator together", () => {
    render(
      transcript({
        messages: [{ role: "user", content: "hi", ts: "t" }],
        thinking: true,
      }),
    );
    expect(screen.getByTestId("chat-user")).toBeInTheDocument();
    expect(screen.getByTestId("thinking")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-empty")).toBeNull();
  });

  it("hides empty hint when queuedMessage is set", () => {
    render(transcript({ queuedMessage: "pending msg" }));
    expect(screen.queryByTestId("chat-empty")).toBeNull();
    expect(screen.getByTestId("queued-indicator")).toBeInTheDocument();
  });

  it("renders QueuedMessageBubble with message text and Queued label", () => {
    render(transcript({ queuedMessage: "hello queue" }));
    const bubble = screen.getByTestId("queued-indicator");
    expect(bubble).toHaveTextContent("hello queue");
    expect(bubble).toHaveTextContent("Queued");
    expect(screen.queryByTestId("queued-cancel")).toBeNull();
  });

  it("calls onCancelQueued when the × button is clicked", () => {
    const onCancel = vi.fn();
    render(transcript({ queuedMessage: "cancel me", onCancelQueued: onCancel }));
    fireEvent.click(screen.getByTestId("queued-cancel"));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("does not render QueuedMessageBubble when queuedMessage is null", () => {
    render(transcript({ queuedMessage: null }));
    expect(screen.queryByTestId("queued-indicator")).toBeNull();
    expect(screen.getByTestId("chat-empty")).toBeInTheDocument();
  });

  it("renders an interrupted assistant message with its partial content and a Stopped label", () => {
    render(
      transcript({
        messages: [
          { role: "assistant", content: "partial reply before stop", ts: "t1", interrupted: true },
        ],
      }),
    );
    const bubble = screen.getByTestId("chat-assistant");
    expect(bubble).toHaveTextContent("partial reply before stop");
    expect(screen.getByTestId("chat-interrupted-label")).toHaveTextContent("Stopped");
  });

  it("renders a placeholder when an interrupted assistant message has no partial content", () => {
    render(
      transcript({
        messages: [{ role: "assistant", content: "", ts: "t1", interrupted: true }],
      }),
    );
    const bubble = screen.getByTestId("chat-assistant");
    expect(bubble).toHaveTextContent("Stopped before replying.");
    expect(screen.getByTestId("chat-interrupted-label")).toBeInTheDocument();
    expect(bubble.querySelector("h4,h5,h6")).toBeNull();
  });

  it("does not show the Stopped label for a normal (non-interrupted) assistant message", () => {
    render(
      transcript({
        messages: [{ role: "assistant", content: "a normal reply", ts: "t1" }],
      }),
    );
    expect(screen.getByTestId("chat-assistant")).toHaveTextContent("a normal reply");
    expect(screen.queryByTestId("chat-interrupted-label")).toBeNull();
  });

  it("does not show the Stopped label for a user message even if interrupted were somehow set", () => {
    render(
      transcript({
        messages: [{ role: "user", content: "hi", ts: "t1", interrupted: true }],
      }),
    );
    expect(screen.queryByTestId("chat-interrupted-label")).toBeNull();
  });

  it("renders a completed assistant message's markdown as formatted output, not literal source characters", () => {
    render(
      transcript({
        messages: [
          {
            role: "assistant",
            content: "# Big heading\n\n- one\n- two\n\n**bold**",
            ts: "t1",
          },
        ],
      }),
    );
    const bubble = screen.getByTestId("chat-assistant");
    expect(screen.getByRole("heading", { level: 4, name: "Big heading" })).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(bubble.querySelector("strong")).toHaveTextContent("bold");
    expect(bubble.querySelector("pre")).toBeNull();
    expect(bubble).not.toHaveTextContent("# Big heading");
  });

  it("renders an interrupted assistant message's partial markdown as literal text, not formatted output", () => {
    render(
      transcript({
        messages: [
          {
            role: "assistant",
            content: "# Big heading\n\n**bold",
            ts: "t1",
            interrupted: true,
          },
        ],
      }),
    );
    const bubble = screen.getByTestId("chat-assistant");
    expect(bubble.querySelector("h4,h5,h6,strong")).toBeNull();
    expect(bubble).toHaveTextContent("# Big heading");
    expect(bubble.querySelector("pre")).not.toBeNull();
  });

  it("renders a user message as literal text even when it contains markdown syntax", () => {
    render(
      transcript({
        messages: [{ role: "user", content: "# not a heading\n**not bold**", ts: "t1" }],
      }),
    );
    const bubble = screen.getByTestId("chat-user");
    expect(bubble.querySelector("h4,h5,h6,strong")).toBeNull();
    expect(bubble).toHaveTextContent("# not a heading");
    expect(bubble.querySelector("pre")).not.toBeNull();
  });

  it("mounts no formatted-markdown output while a turn is in flight and no assistant message has arrived yet", () => {
    render(transcript({ thinking: true, messages: [] }));
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByTestId("thinking")).toBeInTheDocument();
  });

  it("attaches the scroll anchor ref to a sentinel rendered as the transcript's last child", () => {
    const ref = createRef<HTMLDivElement>();
    const { container } = render(
      transcript({
        messages: [{ role: "assistant", content: "hello", ts: "t1" }],
        scrollAnchorRef: ref,
      }),
    );
    const anchor = screen.getByTestId("chat-scroll-anchor");
    expect(ref.current).toBe(anchor);
    expect(container.querySelector('[data-testid="chat-transcript"]')?.lastElementChild).toBe(
      anchor,
    );
  });

  it("gives four activity-line instances (three completed turns plus one live) distinct, non-colliding aria-controls targets", async () => {
    const user = userEvent.setup();
    render(
      transcript({
        messages: [
          { role: "assistant", content: "one", ts: "t1", operations: ["Read: a.py"] },
          { role: "assistant", content: "two", ts: "t2", operations: ["Bash: ls"] },
          { role: "assistant", content: "three", ts: "t3", operations: ["Grep: foo"] },
        ],
        toolActivity: [{ id: "tool-1", label: "Write: d.py" }],
      }),
    );
    const toggles = screen.getAllByTestId("activity-line-toggle");
    expect(toggles).toHaveLength(4);
    for (const toggle of toggles) {
      await user.click(toggle);
    }
    const lists = screen.getAllByTestId("activity-line-list");
    const ids = lists.map((l) => l.id);
    expect(new Set(ids).size).toBe(4);
    toggles.forEach((toggle, idx) => {
      expect(toggle).toHaveAttribute("aria-controls", ids[idx]);
    });
  });

  it("carries no scroll-region classes of its own — a wrapping container owns the overflow instead", () => {
    render(transcript({}));
    const transcriptEl = screen.getByTestId("chat-transcript");
    expect(transcriptEl.className).not.toMatch(/\bflex-1\b/);
    expect(transcriptEl.className).not.toMatch(/\boverflow-y-auto\b/);
  });
});
