import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { api, type DispatchInstructionsMap } from "../../../api";
import { server } from "../../../test/server";
import { useInstructionReadbacks } from "./useInstructionReadbacks";

describe("useInstructionReadbacks", () => {
  it("starts in a loading state with an empty map", () => {
    server.use(
      http.get(
        "/api/workspaces/t1/dispatch-instructions",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { result } = renderHook(() => useInstructionReadbacks("t1"));
    expect(result.current.loading).toBe(true);
    expect(result.current.readbacks).toEqual({});
  });

  it("resolves the fetched map and clears loading", async () => {
    server.use(
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({ inv_001: { status: "attached", text: "do it" } }),
      ),
    );
    const { result } = renderHook(() => useInstructionReadbacks("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.readbacks).toEqual({
      inv_001: { status: "attached", text: "do it" },
    });
  });

  it("degrades to an empty map without throwing on fetch failure", async () => {
    server.use(
      http.get("/api/workspaces/t1/dispatch-instructions", () => HttpResponse.error()),
    );
    const { result } = renderHook(() => useInstructionReadbacks("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.readbacks).toEqual({});
  });

  it("degrades to an empty map on a non-2xx response", async () => {
    server.use(
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const { result } = renderHook(() => useInstructionReadbacks("t1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.readbacks).toEqual({});
  });

  it("returns an empty, non-loading state when taskId is undefined", () => {
    const { result } = renderHook(() => useInstructionReadbacks(undefined));
    expect(result.current.loading).toBe(false);
    expect(result.current.readbacks).toEqual({});
  });

  it("refetches when taskId changes", async () => {
    server.use(
      http.get("/api/workspaces/t1/dispatch-instructions", () =>
        HttpResponse.json({ inv_001: { status: "none", text: null, origin: null } }),
      ),
      http.get("/api/workspaces/t2/dispatch-instructions", () =>
        HttpResponse.json({ inv_002: { status: "unknown", text: null } }),
      ),
    );
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: string }) => useInstructionReadbacks(taskId),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.readbacks).toEqual({ inv_001: { status: "none", text: null, origin: null } });

    rerender({ taskId: "t2" });
    await waitFor(() =>
      expect(result.current.readbacks).toEqual({ inv_002: { status: "unknown", text: null } }),
    );
  });

  it("ignores a resolution that arrives after unmount", async () => {
    let resolve!: (map: DispatchInstructionsMap) => void;
    vi.spyOn(api, "getDispatchInstructions").mockReturnValueOnce(
      new Promise<DispatchInstructionsMap>((r) => {
        resolve = r;
      }),
    );
    const { unmount } = renderHook(() => useInstructionReadbacks("t1"));
    unmount();
    resolve({ inv_001: { status: "attached", text: "x", origin: null } });
    await new Promise((r) => setTimeout(r, 10));
    vi.restoreAllMocks();
  });

  it("ignores a rejection that arrives after unmount", async () => {
    let reject!: (err: unknown) => void;
    vi.spyOn(api, "getDispatchInstructions").mockReturnValueOnce(
      new Promise<DispatchInstructionsMap>((_resolve, r) => {
        reject = r;
      }),
    );
    const { unmount } = renderHook(() => useInstructionReadbacks("t1"));
    unmount();
    reject(new Error("late"));
    await new Promise((r) => setTimeout(r, 10));
    vi.restoreAllMocks();
  });
});
