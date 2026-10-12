import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAction } from "./useAction";

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (err: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAction", () => {
  it("starts idle", () => {
    const { result } = renderHook(() => useAction());
    expect(result.current.busy).toBe(false);
  });

  it("is busy synchronously once a run starts, until it settles", () => {
    const { result } = renderHook(() => useAction());
    const { promise, resolve } = deferred<string>();
    act(() => {
      void result.current.run(() => promise);
    });
    expect(result.current.busy).toBe(true);
    act(() => resolve("done"));
  });

  it("settle clears busy after a resolve", async () => {
    const { result } = renderHook(() => useAction());
    await act(async () => {
      await result.current.run(() => Promise.resolve("ok"));
    });
    expect(result.current.busy).toBe(false);
  });

  it("settle clears busy after a reject", async () => {
    const { result } = renderHook(() => useAction());
    await act(async () => {
      await result.current.run(() => Promise.reject(new Error("boom"))).catch(() => undefined);
    });
    expect(result.current.busy).toBe(false);
  });

  it("rejection policy stays busy after a resolve", async () => {
    const { result } = renderHook(() => useAction("rejection"));
    await act(async () => {
      await result.current.run(() => Promise.resolve("ok"));
    });
    expect(result.current.busy).toBe(true);
  });

  it("rejection policy clears busy after a reject", async () => {
    const { result } = renderHook(() => useAction("rejection"));
    await act(async () => {
      await result.current.run(() => Promise.reject(new Error("boom"))).catch(() => undefined);
    });
    expect(result.current.busy).toBe(false);
  });

  it("passes the resolved value through unchanged", async () => {
    const { result } = renderHook(() => useAction());
    let value: string | undefined;
    await act(async () => {
      value = await result.current.run(() => Promise.resolve("payload"));
    });
    expect(value).toBe("payload");
  });

  it("still rejects to the caller's own catch, with the original error", async () => {
    const { result } = renderHook(() => useAction());
    const error = new Error("boom");
    const caught = vi.fn();
    await act(async () => {
      await result.current.run(() => Promise.reject(error)).catch(caught);
    });
    expect(caught).toHaveBeenCalledWith(error);
  });

  it("clears busy between two sequential runs", async () => {
    const { result } = renderHook(() => useAction());
    await act(async () => {
      await result.current.run(() => Promise.resolve(1));
    });
    expect(result.current.busy).toBe(false);
    await act(async () => {
      await result.current.run(() => Promise.resolve(2));
    });
    expect(result.current.busy).toBe(false);
  });

  it("stays busy throughout two overlapping runs, clearing only once both settle", async () => {
    const { result } = renderHook(() => useAction());
    const first = deferred<string>();
    const second = deferred<string>();
    act(() => {
      void result.current.run(() => first.promise);
    });
    expect(result.current.busy).toBe(true);
    act(() => {
      void result.current.run(() => second.promise);
    });
    expect(result.current.busy).toBe(true);
    await act(async () => {
      first.resolve("a");
      await first.promise;
    });
    expect(result.current.busy).toBe(true);
    await act(async () => {
      second.resolve("b");
      await second.promise;
    });
    expect(result.current.busy).toBe(false);
  });

  it("clears busy and rejects when the thunk throws synchronously instead of returning a promise", async () => {
    const { result } = renderHook(() => useAction());
    const error = new Error("boom");
    const caught = vi.fn();
    await act(async () => {
      await result.current
        .run(() => {
          throw error;
        })
        .catch(caught);
    });
    expect(caught).toHaveBeenCalledWith(error);
    expect(result.current.busy).toBe(false);
  });

  it("begin/end toggle busy directly, without a run", () => {
    const { result } = renderHook(() => useAction());
    act(() => {
      result.current.begin();
    });
    expect(result.current.busy).toBe(true);
    act(() => {
      result.current.end();
    });
    expect(result.current.busy).toBe(false);
  });
});
