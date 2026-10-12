import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { queryClient } from "../api/queryClient";
import { server } from "../test/server";
import { useConsultantLabel, useConsultants } from "./useConsultants";

const CONSULTANTS_PATH = "/api/explore-chat/consultants";
const CONSULTANTS_LIST_KEY = ["consultants", "list"] as const;

const CONSULTANTS = [
  { key: "brainstorming", label: "Brainstorming", description: "Diverge.", team: "brainstorming" },
  { key: "research", label: "Research", description: "Research.", team: "research" },
];

type Step = typeof CONSULTANTS | "error";

function serveConsultants(steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get(CONSULTANTS_PATH, () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      return step === "error" ? HttpResponse.error() : HttpResponse.json(step);
    }),
  );
  return tally;
}

function useLabelAfterLoad(key: string | null) {
  return { consultants: useConsultants(), label: useConsultantLabel(key) };
}

describe("useConsultants", () => {
  it("loads the consultant list and resolves labels by key", async () => {
    serveConsultants([CONSULTANTS]);
    const { result } = renderHook(() => useConsultants());
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.loadFailed).toBe(false);
    expect(result.current.consultants).toHaveLength(2);
    expect(result.current.labelOf("research")).toBe("Research");
  });

  it("sets loadFailed via isError on a failed fetch, with no data ever resolved", async () => {
    serveConsultants(["error"]);
    const { result } = renderHook(() => useConsultants());
    await waitFor(() => {
      expect(result.current.loadFailed).toBe(true);
    });
    expect(result.current.loaded).toBe(true);
    expect(result.current.consultants).toEqual([]);
  });

  it("treats data===undefined with a prior error as loadFailed even when isError is false", () => {
    server.use(http.get(CONSULTANTS_PATH, () => new Promise<Response>(() => undefined)));
    queryClient.getQueryCache().build(
      queryClient,
      { queryKey: CONSULTANTS_LIST_KEY },
      {
        data: undefined,
        dataUpdateCount: 0,
        dataUpdatedAt: 0,
        error: null,
        errorUpdateCount: 1,
        errorUpdatedAt: 0,
        fetchFailureCount: 0,
        fetchFailureReason: null,
        fetchMeta: null,
        isInvalidated: false,
        status: "pending",
        fetchStatus: "idle",
      },
    );
    const { result } = renderHook(() => useConsultants());
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.consultants).toEqual([]);
  });

  it("issues no second fetch across two mounts once the list has loaded once", async () => {
    const tally = serveConsultants([CONSULTANTS]);
    const first = renderHook(() => useConsultants());
    await waitFor(() => {
      expect(first.result.current.loaded).toBe(true);
    });
    first.unmount();
    const second = renderHook(() => useConsultants());
    expect(second.result.current.loaded).toBe(true);
    expect(second.result.current.consultants).toHaveLength(2);
    expect(tally.calls).toBe(1);
  });
});

describe("useConsultantLabel", () => {
  it("returns undefined before the list has loaded", () => {
    server.use(http.get(CONSULTANTS_PATH, () => new Promise(() => undefined)));
    const { result } = renderHook(() => useConsultantLabel("brainstorming"));
    expect(result.current).toBeUndefined();
  });

  it("returns undefined for an unknown key once the list has loaded", async () => {
    serveConsultants([CONSULTANTS]);
    const { result } = renderHook(() => useLabelAfterLoad("mystery_key"));
    await waitFor(() => {
      expect(result.current.consultants.loaded).toBe(true);
    });
    expect(result.current.label).toBeUndefined();
  });

  it("returns undefined for a null key once the list has loaded", async () => {
    serveConsultants([CONSULTANTS]);
    const { result } = renderHook(() => useLabelAfterLoad(null));
    await waitFor(() => {
      expect(result.current.consultants.loaded).toBe(true);
    });
    expect(result.current.label).toBeUndefined();
  });

  it("resolves the label once the list has loaded", async () => {
    serveConsultants([CONSULTANTS]);
    const { result } = renderHook(() => useLabelAfterLoad("brainstorming"));
    await waitFor(() => {
      expect(result.current.label).toBe("Brainstorming");
    });
  });
});
