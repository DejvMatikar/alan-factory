import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { api, type StickyInstructionOut } from "../../../api";
import { server } from "../../../test/server";
import { useStickyInstruction } from "./useStickyInstruction";

describe("useStickyInstruction", () => {
  it("starts in a loading state with no text and no origin", () => {
    server.use(
      http.get(
        "/api/workspaces/t1/sticky-instructions",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    expect(result.current.loading).toBe(true);
    expect(result.current.text).toBeNull();
    expect(result.current.origin).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("resolves the fetched sticky instruction and clears loading", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.text).toBe("focus here");
    expect(result.current.origin).toBe("dispatch");
    expect(result.current.error).toBeNull();
  });

  it("surfaces a fetch failure as error rather than hiding it as empty", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.text).toBeNull();
  });

  it("stringifies a non-Error fetch rejection into the error state", async () => {
    vi.spyOn(api, "getStickyInstructions").mockReturnValueOnce(
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-error"),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("plain-error");
    vi.restoreAllMocks();
  });

  it("returns an empty, non-loading state when taskId is undefined", () => {
    const { result } = renderHook(() => useStickyInstruction(undefined));
    expect(result.current.loading).toBe(false);
    expect(result.current.text).toBeNull();
  });

  it("refetches when taskId changes", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "one", origin: "dispatch" }),
      ),
      http.get("/api/workspaces/t2/sticky-instructions", () =>
        HttpResponse.json({ text: "two", origin: "generated" }),
      ),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useStickyInstruction(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.text).toBe("one");

    rerender({ taskId: "t2" });
    await waitFor(() => expect(result.current.text).toBe("two"));
    expect(result.current.origin).toBe("generated");
  });

  it("clears the previous task's text while a new fetch is pending", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "one", origin: "dispatch" }),
      ),
      http.get(
        "/api/workspaces/t2/sticky-instructions",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useStickyInstruction(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => expect(result.current.text).toBe("one"));

    rerender({ taskId: "t2" });
    await waitFor(() => expect(result.current.text).toBeNull());
    expect(result.current.origin).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it("does not resurrect the previous task's text when the new task's fetch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "one", origin: "dispatch" }),
      ),
      http.get("/api/workspaces/t2/sticky-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useStickyInstruction(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => expect(result.current.text).toBe("one"));

    rerender({ taskId: "t2" });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.text).toBeNull();
  });

  it("nulls text and origin only after clear() resolves", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete("/api/workspaces/t1/sticky-instructions", () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.text).toBe("focus here");

    await act(async () => {
      await result.current.clear();
    });
    expect(result.current.text).toBeNull();
    expect(result.current.origin).toBeNull();
  });

  it("leaves text intact when clear() rejects", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useStickyInstruction("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(
      act(async () => {
        await result.current.clear();
      }),
    ).rejects.toThrow();
    expect(result.current.text).toBe("focus here");
  });

  it("clear() is a no-op when taskId is undefined", async () => {
    const { result } = renderHook(() => useStickyInstruction(undefined));
    await expect(result.current.clear()).resolves.toBeUndefined();
  });

  it("ignores a resolution that arrives after unmount", async () => {
    let resolve!: (value: StickyInstructionOut) => void;
    vi.spyOn(api, "getStickyInstructions").mockReturnValueOnce(
      new Promise<StickyInstructionOut>((r) => {
        resolve = r;
      }),
    );
    const { unmount } = renderHook(() => useStickyInstruction("t1"));
    unmount();
    resolve({ text: "late", origin: "dispatch" });
    await new Promise((r) => setTimeout(r, 10));
    vi.restoreAllMocks();
  });

  it("ignores a rejection that arrives after unmount", async () => {
    let reject!: (err: unknown) => void;
    vi.spyOn(api, "getStickyInstructions").mockReturnValueOnce(
      new Promise<StickyInstructionOut>((_resolve, r) => {
        reject = r;
      }),
    );
    const { unmount } = renderHook(() => useStickyInstruction("t1"));
    unmount();
    reject(new Error("late"));
    await new Promise((r) => setTimeout(r, 10));
    vi.restoreAllMocks();
  });
});
