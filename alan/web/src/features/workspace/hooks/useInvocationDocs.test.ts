import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api";
import { deferredFetch } from "../../../test/docReadHarness";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { useInvocationDocs } from "./useInvocationDocs";

const BASE_DOC = {
  schema_version: "1.0",
  id: "inv_001",
  namespace: "alan.workspace.t1",
  team: "research",
  status: "success",
  started_at: "2026-05-12T08:00:00Z",
  completed_at: "2026-05-12T08:00:30Z",
  tasks: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useInvocationDocs", () => {
  it("returns allSettled=true immediately for empty invIds", async () => {
    const { result } = renderHookWithQuery(() => useInvocationDocs("t1", []));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    expect(result.current.docs.size).toBe(0);
    expect(result.current.errors.size).toBe(0);
  });

  it("populates docs after successful fetch", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json(BASE_DOC),
      ),
    );
    const { result } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    const doc = result.current.docs.get("inv_001");
    expect(doc?.team).toBe("research");
    expect(result.current.errors.get("inv_001")).toBeNull();
  });

  it("populates error on fetch failure", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    const { result } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    expect(result.current.docs.get("inv_001")).toBeNull();
    expect(result.current.errors.get("inv_001")).toContain("404");
  });

  it("no-op on unmount while loading", async () => {
    let resolve: ((v: Response) => void) | undefined;
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001",
        () => new Promise<Response>((r) => { resolve = r; }),
      ),
    );
    const { unmount } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    await new Promise((r) => setTimeout(r, 5));
    unmount();
    resolve!(HttpResponse.json(BASE_DOC));
    await new Promise((r) => setTimeout(r, 10));
  });

  it("stringifies non-Error rejections into the error map", async () => {
    vi.spyOn(api, "getInvocation").mockRejectedValueOnce("plain string error");
    const { result } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    expect(result.current.errors.get("inv_001")).toBe("plain string error");
  });

  it("resets state when invIds change", async () => {
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () =>
        HttpResponse.json(BASE_DOC),
      ),
      http.get("/api/workspaces/t1/invocations/inv_002", () =>
        HttpResponse.json({ ...BASE_DOC, id: "inv_002", team: "architecture" }),
      ),
    );
    let invIds = ["inv_001"] as readonly string[];
    const { result, rerender } = renderHookWithQuery(() => useInvocationDocs("t1", invIds));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    expect(result.current.docs.get("inv_001")?.team).toBe("research");

    invIds = ["inv_002"];
    rerender();
    await waitFor(() => expect(result.current.docs.get("inv_002")?.team).toBe("architecture"));
  });
  it("resets to empty maps that are not settled before the new id list repopulates", async () => {
    const gate = deferredFetch();
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () => HttpResponse.json(BASE_DOC)),
      http.get("/api/workspaces/t1/invocations/inv_002", async () => {
        await gate.settled;
        return HttpResponse.json({ ...BASE_DOC, id: "inv_002", team: "architecture" });
      }),
    );
    const { result, rerender } = renderHookWithQuery(
      ({ invIds }: { invIds: readonly string[] }) => useInvocationDocs("t1", invIds),
      { initialProps: { invIds: ["inv_001"] } },
    );
    await waitFor(() => expect(result.current.allSettled).toBe(true));

    rerender({ invIds: ["inv_001", "inv_002"] });
    expect(result.current.allSettled).toBe(false);
    expect(result.current.docs.size).toBe(0);
    expect(result.current.errors.size).toBe(0);

    gate.release();
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    expect(result.current.docs.get("inv_001")?.team).toBe("research");
    expect(result.current.docs.get("inv_002")?.team).toBe("architecture");
  });

  it("issues no further read when it re-renders with the same task and ids", async () => {
    let calls = 0;
    server.use(
      http.get("/api/workspaces/t1/invocations/inv_001", () => {
        calls += 1;
        return HttpResponse.json(BASE_DOC);
      }),
    );
    const { result, rerender } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.allSettled).toBe(true));
    const settled = result.current;

    rerender();
    await new Promise((r) => setTimeout(r, 10));

    expect(calls).toBe(1);
    expect(result.current).toBe(settled);
  });

  it("hands every loading render the same empty state object", () => {
    server.use(
      http.get(
        "/api/workspaces/t1/invocations/inv_001",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { result, rerender } = renderHookWithQuery(() => useInvocationDocs("t1", ["inv_001"]));
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
    expect(first.allSettled).toBe(false);
  });
});
