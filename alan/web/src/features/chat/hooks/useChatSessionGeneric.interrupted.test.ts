import { act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeBinding, readyHook } from "../../../test/chatSessionHarness";
import { makeChatMessage, makeChatSession } from "../../../test/chatFixtures";

describe("useChatSessionGeneric interrupted turns", () => {
  it("marks the assistant message appended by a turn_stopped event as interrupted", async () => {
    const { result, emit } = await readyHook(makeBinding());
    await emit("turn_stopped", JSON.stringify({ content: "partial" }));
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: "partial",
      interrupted: true,
    });
  });

  it("marks the assistant message appended by an assistant_done event as not interrupted", async () => {
    const { result, emit } = await readyHook(makeBinding());
    await emit("assistant_done", JSON.stringify({ content: "whole" }));
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: "whole",
      interrupted: false,
    });
  });

  it("marks the optimistic user message as not interrupted", async () => {
    const { result } = await readyHook(makeBinding());
    await act(async () => {
      await result.current.sendMessage("hello");
    });
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      content: "hello",
      interrupted: false,
    });
  });

  it("keeps the interrupted flag on a message loaded from the session after a reload", async () => {
    const stopped = makeChatMessage({ content: "partial", interrupted: true });
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(makeChatSession({ messages: [stopped] })),
    });
    const { result } = await readyHook(binding);
    expect(result.current.messages).toEqual([stopped]);
  });
});
