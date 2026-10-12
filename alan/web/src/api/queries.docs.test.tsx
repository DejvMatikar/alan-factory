import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { renderHookWithQuery } from "../test/renderWithQuery";
import { queryClient } from "./queryClient";
import { useDocQuery, useRefetchDoc, useResetDoc, useSetDocData } from "./queries";

type Fetch = (key: string) => Promise<string>;

function useDoc(namespace: string, key: string | undefined, fetch: Fetch) {
  const view = useDocQuery(namespace, key, fetch);
  const reset = useResetDoc(namespace, key);
  return { view, reset };
}

function fetching(value: string) {
  return vi.fn<Fetch>().mockResolvedValue(value);
}

describe("useDocQuery", () => {
  it("reads its key through the fetcher and projects the resolved value", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useDoc("ns", "a", fetch));
    expect(result.current.view.data).toBeUndefined();
    await waitFor(() => {
      expect(result.current.view.data).toBe("payload");
    });
    expect(fetch).toHaveBeenCalledExactlyOnceWith("a");
    expect(result.current.view.isError).toBe(false);
  });

  it("stays pending without fetching while the key is undefined", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useDoc("ns", undefined, fetch));
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(result.current.view).toEqual({ data: undefined, isError: false, error: null });
  });

  it("projects a rejected fetch as an error with the rejection reason and no data", async () => {
    const failure = new Error("boom");
    const fetch = vi.fn<Fetch>().mockRejectedValue(failure);
    const { result } = renderHookWithQuery(() => useDoc("ns", "a", fetch));
    await waitFor(() => {
      expect(result.current.view.isError).toBe(true);
    });
    expect(result.current.view.error).toBe(failure);
    expect(result.current.view.data).toBeUndefined();
  });

  it("keeps two namespaces with the same key in separate entries", async () => {
    const fetchOne = fetching("one");
    const fetchTwo = fetching("two");
    const { result } = renderHookWithQuery(() => ({
      one: useDoc("first", "a", fetchOne),
      two: useDoc("second", "a", fetchTwo),
    }));
    await waitFor(() => {
      expect(result.current.two.view.data).toBe("two");
    });
    expect(result.current.one.view.data).toBe("one");
    expect(fetchOne).toHaveBeenCalledTimes(1);
    expect(fetchTwo).toHaveBeenCalledTimes(1);
  });
});

describe("useResetDoc", () => {
  it("resets only its own key and leaves a sibling key's entry alone", async () => {
    const fetchA = fetching("a");
    const fetchB = fetching("b");
    const { result } = renderHookWithQuery(() => ({
      a: useDoc("ns", "a", fetchA),
      b: useDoc("ns", "b", fetchB),
    }));
    await waitFor(() => {
      expect(result.current.b.view.data).toBe("b");
    });
    await waitFor(() => {
      expect(result.current.a.view.data).toBe("a");
    });

    await act(async () => {
      result.current.a.reset();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(fetchA).toHaveBeenCalledTimes(2);
    });
    expect(fetchB).toHaveBeenCalledTimes(1);
    expect(result.current.b.view.data).toBe("b");
  });

  it("is a safe no-op for a key that has no entry", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useDoc("ns", undefined, fetch));
    await act(async () => {
      result.current.reset();
      await Promise.resolve();
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("hands back a stable callback across renders of the same key", () => {
    const fetch = fetching("payload");
    const { result, rerender } = renderHookWithQuery(() => useDoc("ns", "a", fetch));
    const first = result.current.reset;
    rerender();
    expect(result.current.reset).toBe(first);
  });
});

let visibilitySpy: MockInstance<() => DocumentVisibilityState> | null = null;

function setVisibility(state: DocumentVisibilityState): void {
  visibilitySpy?.mockRestore();
  visibilitySpy = vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
}

afterEach(() => {
  visibilitySpy?.mockRestore();
  visibilitySpy = null;
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function deferredFetch() {
  const resolvers: ((value: string) => void)[] = [];
  const fetch = vi.fn<Fetch>(
    () =>
      new Promise<string>((resolve) => {
        resolvers.push(resolve);
      }),
  );
  return { fetch, resolvers };
}

describe("useDocQuery pollMs", () => {
  it("re-runs the fetcher on the poll interval while the tab is visible", async () => {
    const fetch = fetching("payload");
    renderHookWithQuery(() => useDocQuery("ns", "a", fetch, { pollMs: 20 }));
    await waitFor(() => {
      expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(3);
    });
  });

  it("pauses the poll while document.visibilityState is hidden and resumes once visible", async () => {
    const fetch = fetching("payload");
    renderHookWithQuery(() => useDocQuery("ns", "a", fetch, { pollMs: 20 }));
    await waitFor(() => {
      expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    setVisibility("hidden");
    await sleep(40);
    const pausedAt = fetch.mock.calls.length;
    await sleep(100);
    expect(fetch.mock.calls.length).toBe(pausedAt);

    setVisibility("visible");
    await waitFor(() => {
      expect(fetch.mock.calls.length).toBeGreaterThan(pausedAt);
    });
  });

  it("does not poll when no interval is given", async () => {
    const fetch = fetching("payload");
    renderHookWithQuery(() => useDocQuery("ns", "a", fetch));
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1);
    });
    await sleep(80);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("useRefetchDoc", () => {
  function useRefetching(fetch: Fetch) {
    return { view: useDocQuery("ns", "a", fetch), refetch: useRefetchDoc("ns", "a") };
  }

  it("merges into a first load that is still running without a second request", async () => {
    const { fetch, resolvers } = deferredFetch();
    const { result } = renderHookWithQuery(() => useRefetching(fetch));
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      result.current.refetch();
      await Promise.resolve();
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolvers[0]!("first");
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.view.data).toBe("first");
    });
  });

  it("replaces an in-flight fetch once data exists and keeps only the newest result", async () => {
    const fetch = vi
      .fn<Fetch>()
      .mockResolvedValueOnce("one")
      .mockImplementationOnce(
        () =>
          new Promise<string>((resolve) => {
            resolveSecond = resolve;
          }),
      )
      .mockResolvedValueOnce("three");
    let resolveSecond: (value: string) => void = () => undefined;
    const { result } = renderHookWithQuery(() => useRefetching(fetch));
    await waitFor(() => {
      expect(result.current.view.data).toBe("one");
    });

    await act(async () => {
      result.current.refetch();
      await Promise.resolve();
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => {
      result.current.refetch();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.view.data).toBe("three");
    });
    expect(fetch).toHaveBeenCalledTimes(3);

    await act(async () => {
      resolveSecond("two");
      await sleep(20);
    });
    expect(result.current.view.data).toBe("three");
  });

  it("is a safe no-op for a key that has no entry", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => ({
      view: useDocQuery("ns", undefined, fetch),
      refetch: useRefetchDoc("ns", undefined),
    }));
    await act(async () => {
      result.current.refetch();
      await Promise.resolve();
    });
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("useSetDocData", () => {
  function useSetting(namespace: string, key: string | undefined, fetch: Fetch) {
    return { view: useDocQuery(namespace, key, fetch), set: useSetDocData<string>(namespace, key) };
  }

  it("writes the loaded entry through the updater without a network call", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useSetting("ns", "a", fetch));
    await waitFor(() => {
      expect(result.current.view.data).toBe("payload");
    });
    await act(async () => {
      result.current.set((previous) => `${previous ?? ""}!`);
      await Promise.resolve();
    });
    expect(result.current.view.data).toBe("payload!");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("leaves the entry alone when the updater returns undefined", async () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useSetting("ns", "a", fetch));
    await waitFor(() => {
      expect(result.current.view.data).toBe("payload");
    });
    await act(async () => {
      result.current.set(() => undefined);
      await Promise.resolve();
    });
    expect(result.current.view.data).toBe("payload");
  });

  it("never creates an entry for a key that was not loaded", () => {
    const { result } = renderHookWithQuery(() => useSetDocData<string>("ns", "missing"));
    result.current(() => "written");
    expect(queryClient.getQueryState(["doc", "ns", "missing"])).toBeUndefined();
  });

  it("never writes while the first load is still in flight", async () => {
    const fetch = vi.fn<Fetch>(() => new Promise<string>(() => undefined));
    const { result } = renderHookWithQuery(() => useSetting("ns", "a", fetch));
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      result.current.set(() => "written");
      await Promise.resolve();
    });
    expect(result.current.view.data).toBeUndefined();
    expect(queryClient.getQueryData(["doc", "ns", "a"])).toBeUndefined();
  });

  it("never writes after the first load failed", async () => {
    const fetch = vi.fn<Fetch>().mockRejectedValue(new Error("boom"));
    const { result } = renderHookWithQuery(() => useSetting("ns", "a", fetch));
    await waitFor(() => {
      expect(result.current.view.isError).toBe(true);
    });
    await act(async () => {
      result.current.set(() => "written");
      await Promise.resolve();
    });
    expect(result.current.view.data).toBeUndefined();
    expect(queryClient.getQueryData(["doc", "ns", "a"])).toBeUndefined();
  });

  it("never writes while the key is undefined", () => {
    const fetch = fetching("payload");
    const { result } = renderHookWithQuery(() => useSetting("ns", undefined, fetch));
    result.current.set(() => "written");
    expect(result.current.view.data).toBeUndefined();
    expect(queryClient.getQueryState(["doc", "ns", null])?.data).toBeUndefined();
  });
});
