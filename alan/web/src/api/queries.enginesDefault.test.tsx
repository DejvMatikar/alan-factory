import { act, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { LISTED_DEFAULT_KEY, makeEngineSummaryFor } from "../test/engineFixtures";
import { renderHookWithQuery } from "../test/renderWithQuery";
import { server } from "../test/server";
import {
  useEnginesListQuery,
  useInvalidateEnginesList,
  useListedDefaultEngineKeyQuery,
} from "./queries";
import { queryClient } from "./queryClient";

const LIST_KEY = ["engines", "list"];
const DEFAULT_KEY = ["engines", "default"];
const OTHER_DEFAULT_KEY = "acme/other-default";
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

const ENGINE = makeEngineSummaryFor("acme/widget");
const LISTING = { engines: [ENGINE], default_engine_key: LISTED_DEFAULT_KEY };
const LISTING_WITHOUT_DEFAULT = { engines: [ENGINE] };

type Step = Record<string, unknown> | "error" | "unavailable";

function serveListings(steps: Step[]) {
  const tally = { calls: 0 };
  server.use(
    http.get("/api/engines", () => {
      const step = steps[Math.min(tally.calls, steps.length - 1)]!;
      tally.calls += 1;
      if (step === "error") return HttpResponse.error();
      if (step === "unavailable") {
        return HttpResponse.json({ detail: "engine registry unavailable" }, { status: 503 });
      }
      return HttpResponse.json(step);
    }),
  );
  return tally;
}

function useListAndListedDefault() {
  return {
    list: useEnginesListQuery(),
    listedDefault: useListedDefaultEngineKeyQuery(),
    invalidate: useInvalidateEnginesList(),
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  });
}

describe("engines list query default sibling", () => {
  it("keeps the list entry an array of engines", async () => {
    serveListings([LISTING]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.list.isSuccess).toBe(true);
    });
    expect(queryClient.getQueryData(LIST_KEY)).toEqual([ENGINE]);
    expect(result.current.list.data).toEqual([ENGINE]);
  });

  it("stores the listed default key in the sibling entry", async () => {
    serveListings([LISTING]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBe(LISTED_DEFAULT_KEY);
  });

  it("stores null in the sibling entry when the response omits the default key", async () => {
    serveListings([LISTING_WITHOUT_DEFAULT]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.list.isSuccess).toBe(true);
    });
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBeNull();
    expect(result.current.listedDefault).toBeNull();
  });

  it("stores null in the sibling entry when the response names a null default key", async () => {
    serveListings([{ engines: [ENGINE], default_engine_key: null }]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.list.isSuccess).toBe(true);
    });
    expect(result.current.listedDefault).toBeNull();
  });

  it("fills both entries from a single request", async () => {
    const tally = serveListings([LISTING]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    await settle();
    expect(tally.calls).toBe(1);
    expect(
      queryClient
        .getQueryCache()
        .getAll()
        .map((query) => query.queryKey.join("/"))
        .sort(),
    ).toEqual(["engines/default", "engines/list"]);
  });

  it("issues no request when only the default sibling is observed", async () => {
    const tally = serveListings([LISTING]);
    const { result } = renderHookWithQuery(() => useListedDefaultEngineKeyQuery());
    await settle();
    expect(tally.calls).toBe(0);
    expect(result.current).toBeUndefined();
  });

  it("reports undefined from the sibling until the list has loaded", async () => {
    serveListings([LISTING]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    expect(result.current.listedDefault).toBeUndefined();
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
  });

  it("updates both entries when an invalidation refetch names a different default", async () => {
    const tally = serveListings([
      LISTING,
      { engines: [ENGINE, makeEngineSummaryFor(OTHER_DEFAULT_KEY)], default_engine_key: OTHER_DEFAULT_KEY },
    ]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(OTHER_DEFAULT_KEY);
    });
    await waitFor(() => {
      expect(result.current.list.data).toHaveLength(2);
    });
    expect(tally.calls).toBe(2);
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBe(OTHER_DEFAULT_KEY);
    expect(queryClient.getQueryData(LIST_KEY)).toHaveLength(2);
  });

  it("clears the sibling to null when an invalidation refetch drops the default key", async () => {
    serveListings([LISTING, LISTING_WITHOUT_DEFAULT]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.listedDefault).toBeNull();
    });
  });

  it("invalidates only the list entry, never the sibling", async () => {
    serveListings([LISTING, LISTING]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    await settle();
    act(() => {
      result.current.invalidate();
    });
    expect(queryClient.getQueryState(LIST_KEY)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(DEFAULT_KEY)?.isInvalidated).toBe(false);
    await settle();
  });

  it.each([
    { failure: "a network error", step: "error" as const },
    { failure: "a non-2xx response", step: "unavailable" as const },
  ])("leaves the sibling undefined and errors the list on $failure", async ({ step }) => {
    serveListings([step]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.list.isError).toBe(true);
    });
    expect(result.current.listedDefault).toBeUndefined();
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBeUndefined();
    expect(queryClient.getQueryState(DEFAULT_KEY)?.status).toBe("pending");
  });

  it("keeps the previous sibling value when a later refetch fails", async () => {
    serveListings([LISTING, "error"]);
    const { result } = renderHookWithQuery(() => useListAndListedDefault());
    await waitFor(() => {
      expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    });
    act(() => {
      result.current.invalidate();
    });
    await waitFor(() => {
      expect(result.current.list.isError).toBe(true);
    });
    expect(result.current.listedDefault).toBe(LISTED_DEFAULT_KEY);
    expect(queryClient.getQueryData(LIST_KEY)).toEqual([ENGINE]);
  });
});

describe("engines default sibling retention", () => {
  it("registers an infinite gcTime for the sibling and none for the list", () => {
    expect(queryClient.getQueryDefaults(DEFAULT_KEY).gcTime).toBe(Infinity);
    expect(queryClient.getQueryDefaults(LIST_KEY).gcTime).toBeUndefined();
  });

  it("keeps the sibling after the list entry is collected with no observers left", async () => {
    serveListings([LISTING]);
    const { result, unmount } = renderHookWithQuery(() => useEnginesListQuery());
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBe(LISTED_DEFAULT_KEY);
    vi.useFakeTimers();
    unmount();
    vi.advanceTimersByTime(ONE_DAY_MS);
    expect(queryClient.getQueryData(LIST_KEY)).toBeUndefined();
    expect(queryClient.getQueryCache().find({ queryKey: DEFAULT_KEY })).toBeDefined();
    expect(queryClient.getQueryData(DEFAULT_KEY)).toBe(LISTED_DEFAULT_KEY);
  });

  it("serves the retained sibling to a later observer without a request", async () => {
    const tally = serveListings([LISTING]);
    const first = renderHookWithQuery(() => useEnginesListQuery());
    await waitFor(() => {
      expect(first.result.current.isSuccess).toBe(true);
    });
    vi.useFakeTimers();
    first.unmount();
    vi.advanceTimersByTime(ONE_DAY_MS);
    vi.useRealTimers();
    const second = renderHookWithQuery(() => useListedDefaultEngineKeyQuery());
    expect(second.result.current).toBe(LISTED_DEFAULT_KEY);
    await settle();
    expect(tally.calls).toBe(1);
  });
});
