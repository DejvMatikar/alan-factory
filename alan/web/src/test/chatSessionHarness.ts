import { act, renderHook, waitFor, type RenderHookOptions } from "@testing-library/react";
import { vi } from "vitest";

import type { ChatEventHandlers } from "../api";
import { useChatSessionGeneric, type ChatSessionApi } from "../features/chat/hooks/useChatSessionGeneric";
import { makeChatSession } from "./chatFixtures";

export const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
export const SID2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

export interface TestBrief {
  content: string;
}

export function makeBinding(
  overrides: Partial<ChatSessionApi<TestBrief>> = {},
): ChatSessionApi<TestBrief> {
  return {
    family: "test",
    get: vi.fn().mockResolvedValue(makeChatSession()),
    subscribe: vi.fn().mockReturnValue(() => undefined),
    send: vi.fn().mockResolvedValue({ status: "accepted" }),
    interrupt: vi.fn().mockResolvedValue(undefined),
    summarize: vi.fn().mockResolvedValue({ content: "summary" }),
    putBrief: vi.fn().mockResolvedValue({ content: "updated" }),
    delete: vi.fn().mockResolvedValue(undefined),
    createNew: vi.fn().mockResolvedValue({ session_id: SID2 }),
    noSessionError: "no session",
    ...overrides,
  };
}

export async function readyHook(
  binding: ChatSessionApi<TestBrief>,
  sessionId: string = SID,
  wrapper?: RenderHookOptions<unknown>["wrapper"],
) {
  const handlers: { current: ChatEventHandlers | null } = { current: null };
  vi.mocked(binding.subscribe).mockImplementation((_sid, h) => {
    handlers.current = h;
    return () => undefined;
  });
  const hook = renderHook(
    () => useChatSessionGeneric<TestBrief>(sessionId, binding),
    wrapper === undefined ? undefined : { wrapper },
  );
  await waitFor(() => {
    if (hook.result.current.session.kind !== "ready") throw new Error("not ready");
  });
  return {
    ...hook,
    emit: (event: string, data: string) =>
      act(async () => {
        handlers.current?.onEvent({ event, data });
        await Promise.resolve();
      }),
  };
}
