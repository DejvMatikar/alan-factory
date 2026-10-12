import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../../../api";
import { makeChatMessage } from "../../../test/chatFixtures";
import ChatTranscript from "./ChatTranscript";

function renderTranscript(messages: ChatMessage[]) {
  return render(
    <ChatTranscript
      scrollAnchorRef={createRef<HTMLDivElement>()}
      messages={messages}
      toolActivity={[]}
      thinking={false}
      emptyHint="hint"
    />,
  );
}

describe("ChatTranscript interrupted label", () => {
  it("labels an assistant message the wire reports as interrupted", () => {
    renderTranscript([makeChatMessage({ content: "partial", interrupted: true })]);
    expect(screen.getByTestId("chat-interrupted-label")).toHaveTextContent("Stopped");
  });

  it("does not label an assistant message the wire reports as not interrupted", () => {
    renderTranscript([makeChatMessage({ content: "whole", interrupted: false })]);
    expect(screen.queryByTestId("chat-interrupted-label")).not.toBeInTheDocument();
  });

  it("labels only the interrupted message in a mixed transcript", () => {
    renderTranscript([
      makeChatMessage({ content: "whole" }),
      makeChatMessage({ content: "partial", interrupted: true }),
    ]);
    expect(screen.getAllByTestId("chat-interrupted-label")).toHaveLength(1);
  });
});
