import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import { useCollisionCheck } from "./useCollisionCheck";

describe("useCollisionCheck", () => {
  it("returns green immediately for empty brief without fetching", () => {
    const { result } = renderHook(() => useCollisionCheck(""));
    expect(result.current.collisionInfo).toEqual({
      status: "green",
      sentence: "No overlapping work found.",
      match: null,
    });
  });

  it("returns green immediately for whitespace-only brief without fetching", () => {
    const { result } = renderHook(() => useCollisionCheck("   "));
    expect(result.current.collisionInfo).toEqual({
      status: "green",
      sentence: "No overlapping work found.",
      match: null,
    });
  });

  it("starts loading for non-empty brief then resolves to API response", async () => {
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({
          state: "yellow",
          sentence: "Similar work in progress.",
          match: { label: "PR #99", kind: "pr", deep_link: "https://github.com/pr/99" },
        }),
      ),
    );
    const { result } = renderHook(() => useCollisionCheck("build a feature"));
    expect(result.current.collisionInfo.status).toBe("loading");
    await waitFor(() => {
      expect(result.current.collisionInfo.status).toBe("yellow");
    });
    expect(result.current.collisionInfo.sentence).toBe("Similar work in progress.");
    expect(result.current.collisionInfo.match).toEqual({
      label: "PR #99",
      kind: "pr",
      deep_link: "https://github.com/pr/99",
    });
  });

  it("degrades to green on API error", async () => {
    server.use(
      http.post("/api/collision-check", () => new HttpResponse(null, { status: 500 })),
    );
    const { result } = renderHook(() => useCollisionCheck("some brief"));
    await waitFor(() => {
      expect(result.current.collisionInfo.status).toBe("green");
    });
    expect(result.current.collisionInfo.sentence).toBe("No overlapping work found.");
  });

  it("rerun() with empty string returns green without fetching", () => {
    const { result } = renderHook(() => useCollisionCheck(""));
    act(() => {
      void result.current.rerun("");
    });
    expect(result.current.collisionInfo).toEqual({
      status: "green",
      sentence: "No overlapping work found.",
      match: null,
    });
  });

  it("rerun() shows loading then resolves to new result", async () => {
    const { result } = renderHook(() => useCollisionCheck(""));
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({
          state: "red",
          sentence: "Overlapping live work found.",
          match: {
            label: "workspace-123",
            kind: "workspace",
            deep_link: "/workspaces/workspace-123",
          },
        }),
      ),
    );
    act(() => {
      void result.current.rerun("updated brief text");
    });
    expect(result.current.collisionInfo.status).toBe("loading");
    await waitFor(() => {
      expect(result.current.collisionInfo.status).toBe("red");
    });
    expect(result.current.collisionInfo.match?.label).toBe("workspace-123");
  });

  it("rerun() degrades to green on API error", async () => {
    server.use(
      http.post("/api/collision-check", () => new HttpResponse(null, { status: 503 })),
    );
    const { result } = renderHook(() => useCollisionCheck(""));
    act(() => {
      void result.current.rerun("some text");
    });
    await waitFor(() => {
      expect(result.current.collisionInfo.status).toBe("green");
    });
  });

  it("does not auto-fire when auto is false, even with a non-empty initial brief", async () => {
    let calls = 0;
    server.use(
      http.post("/api/collision-check", () => {
        calls += 1;
        return HttpResponse.json({ state: "red", sentence: "x", match: null });
      }),
    );
    const { result } = renderHook(() => useCollisionCheck("existing brief", { auto: false }));
    expect(result.current.collisionInfo).toEqual({
      status: "green",
      sentence: "No overlapping work found.",
      match: null,
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(calls).toBe(0);
  });

  it("rerun() still fires a request when auto is false", async () => {
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "yellow", sentence: "maybe", match: null }),
      ),
    );
    const { result } = renderHook(() => useCollisionCheck("", { auto: false }));
    act(() => {
      void result.current.rerun("brief text");
    });
    expect(result.current.collisionInfo.status).toBe("loading");
    await waitFor(() => expect(result.current.collisionInfo.status).toBe("yellow"));
  });

  it("includes chatFamily and sessionId in the request body when provided", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/collision-check", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    const { result } = renderHook(() =>
      useCollisionCheck("", { auto: false, chatFamily: "build", sessionId: "sid-1" }),
    );
    act(() => {
      void result.current.rerun("brief text");
    });
    await waitFor(() =>
      expect(body).toEqual({ brief: "brief text", chat_family: "build", session_id: "sid-1" }),
    );
  });

  it("omits chatFamily and sessionId from the request body when not provided", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/collision-check", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    const { result } = renderHook(() => useCollisionCheck(""));
    act(() => {
      void result.current.rerun("brief text");
    });
    await waitFor(() => expect(body).toEqual({ brief: "brief text" }));
  });

  it("calls onSettled after a check resolves", async () => {
    const onSettled = vi.fn();
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "green", sentence: "", match: null }),
      ),
    );
    const { result } = renderHook(() => useCollisionCheck("", { auto: false, onSettled }));
    act(() => {
      void result.current.rerun("brief text");
    });
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
  });

  it("does not call onSettled when rerun is a no-op for an empty brief", () => {
    const onSettled = vi.fn();
    const { result } = renderHook(() => useCollisionCheck("", { auto: false, onSettled }));
    act(() => {
      void result.current.rerun("   ");
    });
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("serializes an overlapping rerun so requests never run concurrently", async () => {
    const events: string[] = [];
    let resolveFirst!: () => void;
    let callIndex = 0;
    server.use(
      http.post("/api/collision-check", async () => {
        const idx = ++callIndex;
        events.push(`start-${idx}`);
        if (idx === 1) {
          await new Promise<void>((res) => {
            resolveFirst = res;
          });
        }
        events.push(`end-${idx}`);
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    const { result } = renderHook(() => useCollisionCheck("", { auto: false }));
    act(() => {
      void result.current.rerun("first brief");
    });
    act(() => {
      void result.current.rerun("second brief");
    });
    await waitFor(() => expect(events).toContain("start-1"));
    expect(events).not.toContain("start-2");
    resolveFirst();
    await waitFor(() => expect(events).toEqual(["start-1", "end-1", "start-2", "end-2"]));
  });

  it("resolves rerun()'s returned promise to the settled collision info", async () => {
    server.use(
      http.post("/api/collision-check", () =>
        HttpResponse.json({ state: "yellow", sentence: "maybe", match: null }),
      ),
    );
    const { result } = renderHook(() => useCollisionCheck(""));
    let resolved: unknown;
    await act(async () => {
      resolved = await result.current.rerun("brief text");
    });
    expect(resolved).toEqual({ status: "yellow", sentence: "maybe", match: null });
  });

  it("resolves rerun()'s returned promise to green for an empty brief", async () => {
    const { result } = renderHook(() => useCollisionCheck(""));
    const resolved = await result.current.rerun("");
    expect(resolved).toEqual({ status: "green", sentence: "No overlapping work found.", match: null });
  });

  it("keeps firing later checks even if onSettled throws", async () => {
    let calls = 0;
    const onSettled = vi.fn(() => {
      throw new Error("boom");
    });
    server.use(
      http.post("/api/collision-check", () => {
        calls += 1;
        return HttpResponse.json({ state: "green", sentence: "", match: null });
      }),
    );
    const { result } = renderHook(() => useCollisionCheck("", { auto: false, onSettled }));
    act(() => {
      void result.current.rerun("first brief");
    });
    await waitFor(() => expect(calls).toBe(1));
    act(() => {
      void result.current.rerun("second brief");
    });
    await waitFor(() => expect(calls).toBe(2));
  });
});
