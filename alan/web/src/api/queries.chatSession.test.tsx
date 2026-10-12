import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RepoProvisionStatus, RunSummary } from "../api";
import { fetchChatSession, useChatSessionQuery, useInvalidateChatSession } from "./queries";
import { queryClient } from "./queryClient";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const OTHER_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

interface TestChatSessionWire {
  session_id: string;
  messages: { role: string; content: string; ts: string; interrupted: boolean }[];
  brief: { content: string; updated_at: string } | null;
  run_summary: RunSummary;
  collision_run_summary: RunSummary | null;
  project_id: string | null;
  repo_provision: RepoProvisionStatus;
  engine_key: string | null;
}

function makeSession(overrides: Partial<TestChatSessionWire> = {}): TestChatSessionWire {
  return {
    session_id: SID,
    messages: [{ role: "user", content: "hi", ts: "2026-06-01T00:00:00Z", interrupted: false }],
    brief: { content: "brief text", updated_at: "2026-06-01T00:00:00Z" },
    run_summary: { model: "opus", effort: "xhigh", system_prompt: "prompt" },
    collision_run_summary: null,
    project_id: null,
    repo_provision: "not_needed",
    engine_key: null,
    ...overrides,
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

describe("useChatSessionQuery", () => {
  it("keys the read by chat-session, family and session id, and no other", async () => {
    const get = vi.fn().mockResolvedValue(makeSession());
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(queryClient.getQueryCache().getAll().map((q) => q.queryKey)).toEqual([
      ["chat-session", "build", SID],
    ]);
  });

  it("projects only runSummary, collisionRunSummary, projectId, repoProvision and engineKey — never messages or brief", async () => {
    const get = vi.fn().mockResolvedValue(makeSession({ project_id: "proj-1", engine_key: "acme/x" }));
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(Object.keys(result.current.data!).sort()).toEqual(
      ["collisionRunSummary", "engineKey", "projectId", "repoProvision", "runSummary"].sort(),
    );
    expect(result.current.data!.projectId).toBe("proj-1");
    expect(result.current.data!.engineKey).toBe("acme/x");
  });

  it("defaults collisionRunSummary and projectId to null when the wire omits them", async () => {
    const get = vi.fn().mockResolvedValue(makeSession());
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.collisionRunSummary).toBeNull();
    expect(result.current.data!.projectId).toBeNull();
  });

  it("defaults engineKey to undefined when the wire's engine_key is null", async () => {
    const get = vi.fn().mockResolvedValue(makeSession({ engine_key: null }));
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data!.engineKey).toBeUndefined();
  });

  it("issues no request and reports no data while sessionId is null", async () => {
    const get = vi.fn().mockResolvedValue(makeSession());
    const { result } = renderHook(() => useChatSessionQuery("build", null, get));
    await settle();
    expect(get).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
    expect(result.current.isError).toBe(false);
  });

  it("keeps build and explore families of the same session id apart", async () => {
    const buildGet = vi.fn().mockResolvedValue(makeSession({ project_id: "build-proj" }));
    const exploreGet = vi.fn().mockResolvedValue(makeSession({ project_id: "explore-proj" }));
    const { result } = renderHook(() => ({
      build: useChatSessionQuery("build", SID, buildGet),
      explore: useChatSessionQuery("explore", SID, exploreGet),
    }));
    await waitFor(() => {
      expect(result.current.build.data?.projectId).toBe("build-proj");
      expect(result.current.explore.data?.projectId).toBe("explore-proj");
    });
    expect(buildGet).toHaveBeenCalledTimes(1);
    expect(exploreGet).toHaveBeenCalledTimes(1);
  });

  it("applies the read overrides: staleTime 0, refetchOnMount always, gcTime 0, no placeholder and background polling", async () => {
    const get = vi.fn().mockResolvedValue(makeSession());
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    await waitFor(() => expect(result.current.data).toBeDefined());
    const query = queryClient.getQueryCache().find({ queryKey: ["chat-session", "build", SID] })!;
    const options = query.observers[0]!.options;
    expect(options.staleTime).toBe(0);
    expect(options.refetchOnMount).toBe("always");
    expect(options.gcTime).toBe(0);
    expect(options.refetchIntervalInBackground).toBe(true);
    expect((options.placeholderData as () => unknown)()).toBeUndefined();
  });

  it("polls every 1500ms while repo_provision is pending and stops once it resolves", async () => {
    vi.useFakeTimers();
    const get = vi
      .fn()
      .mockResolvedValueOnce(makeSession({ repo_provision: "pending" }))
      .mockResolvedValueOnce(makeSession({ repo_provision: "pending" }))
      .mockResolvedValueOnce(makeSession({ repo_provision: "ready" }));
    try {
      const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(get).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(2);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(get).toHaveBeenCalledTimes(3);
      expect(result.current.data?.repoProvision).toBe("ready");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      expect(get).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("fetchChatSession", () => {
  it("resolves the full wire response, including messages and brief", async () => {
    const get: (id: string) => Promise<TestChatSessionWire> = vi.fn().mockResolvedValue(makeSession());
    const session = await fetchChatSession("build", SID, get);
    expect(session.messages).toHaveLength(1);
    expect(session.brief).toEqual({ content: "brief text", updated_at: "2026-06-01T00:00:00Z" });
  });

  it("joins an in-flight observer fetch instead of issuing a second network call", async () => {
    let resolveGet!: (v: ReturnType<typeof makeSession>) => void;
    const get = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveGet = resolve;
      }),
    );
    const { result } = renderHook(() => useChatSessionQuery("build", SID, get));
    expect(get).toHaveBeenCalledTimes(1);
    const pending = fetchChatSession("build", SID, get);
    resolveGet(makeSession({ project_id: "proj-9" }));
    const session = await pending;
    expect(get).toHaveBeenCalledTimes(1);
    expect(session.project_id).toBe("proj-9");
    await waitFor(() => expect(result.current.data?.projectId).toBe("proj-9"));
  });
});

describe("useInvalidateChatSession", () => {
  it("refetches only the invalidated family and session", async () => {
    const buildGet = vi.fn().mockResolvedValue(makeSession());
    const otherSessionGet = vi.fn().mockResolvedValue(makeSession());
    const { result } = renderHook(() => ({
      a: useChatSessionQuery("build", SID, buildGet),
      b: useChatSessionQuery("build", OTHER_SID, otherSessionGet),
      invalidate: useInvalidateChatSession("build", SID),
    }));
    await waitFor(() => {
      expect(result.current.a.data).toBeDefined();
      expect(result.current.b.data).toBeDefined();
    });
    await act(async () => {
      await result.current.invalidate();
    });
    expect(buildGet).toHaveBeenCalledTimes(2);
    expect(otherSessionGet).toHaveBeenCalledTimes(1);
  });

  it("returns nothing a caller could leave unhandled, and the same function across re-renders", async () => {
    const { result, rerender } = renderHook(() => useInvalidateChatSession("build", SID));
    const before = result.current;
    rerender();
    expect(result.current).toBe(before);
    expect(result.current()).toBeInstanceOf(Promise);
    await settle();
  });
});
