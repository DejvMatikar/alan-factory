import { act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderHookWithQuery } from "../test/renderWithQuery";
import { useActionMutation } from "./queries";

function pendingAction() {
  let resolveAction!: (value: string) => void;
  let rejectAction!: (reason: unknown) => void;
  const action = vi.fn(
    () =>
      new Promise<string>((resolve, reject) => {
        resolveAction = resolve;
        rejectAction = reject;
      }),
  );
  return {
    action,
    resolveAction: (value: string) => resolveAction(value),
    rejectAction: (reason: unknown) => rejectAction(reason),
  };
}

async function startRun(
  result: { current: { run: (callbacks?: { onSuccess?: (v: string) => void; onError?: (e: unknown) => void }) => void } },
  callbacks: { onSuccess?: (v: string) => void; onError?: (e: unknown) => void },
) {
  await act(async () => {
    result.current.run(callbacks);
    await Promise.resolve();
  });
}

describe("useActionMutation", () => {
  it("reports pending while the action runs and success once it resolves", async () => {
    const { action, resolveAction } = pendingAction();
    const { result } = renderHookWithQuery(() => useActionMutation(action));
    expect(result.current.isPending).toBe(false);
    await startRun(result, {});
    expect(result.current.isPending).toBe(true);
    await act(async () => {
      resolveAction("done");
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.isPending).toBe(false);
  });

  it("calls the success callback with the resolved value while mounted", async () => {
    const { action, resolveAction } = pendingAction();
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result } = renderHookWithQuery(() => useActionMutation(action));
    await startRun(result, { onSuccess, onError });
    await act(async () => {
      resolveAction("done");
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledExactlyOnceWith("done");
    });
    expect(onError).not.toHaveBeenCalled();
  });

  it("calls the error callback with the rejection while mounted and exposes the error", async () => {
    const { action, rejectAction } = pendingAction();
    const failure = new Error("boom");
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result } = renderHookWithQuery(() => useActionMutation(action));
    await startRun(result, { onSuccess, onError });
    await act(async () => {
      rejectAction(failure);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(onError).toHaveBeenCalledExactlyOnceWith(failure);
    });
    expect(onSuccess).not.toHaveBeenCalled();
    expect(result.current.isError).toBe(true);
    expect(result.current.error).toBe(failure);
    expect(result.current.isPending).toBe(false);
  });

  it("runs to completion without callbacks whether the action succeeds or fails", async () => {
    const settle = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce("done")
      .mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHookWithQuery(() => useActionMutation(settle));
    await act(async () => {
      result.current.run();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await act(async () => {
      result.current.run();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });

  it("tolerates a missing error callback when the action fails", async () => {
    const onSuccess = vi.fn();
    const failing = vi.fn<() => Promise<string>>().mockRejectedValue(new Error("boom"));
    const { result } = renderHookWithQuery(() => useActionMutation(failing));
    await act(async () => {
      result.current.run({ onSuccess });
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("tolerates a missing success callback when the action succeeds", async () => {
    const onError = vi.fn();
    const succeeding = vi.fn<() => Promise<string>>().mockResolvedValue("done");
    const { result } = renderHookWithQuery(() => useActionMutation(succeeding));
    await act(async () => {
      result.current.run({ onError });
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(onError).not.toHaveBeenCalled();
  });

  it("skips the success callback when the action resolves after unmount", async () => {
    const { action, resolveAction } = pendingAction();
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result, unmount } = renderHookWithQuery(() => useActionMutation(action));
    await startRun(result, { onSuccess, onError });
    unmount();
    await act(async () => {
      resolveAction("late");
      await Promise.resolve();
    });
    expect(action).toHaveBeenCalledOnce();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("skips the error callback when the action rejects after unmount", async () => {
    const { action, rejectAction } = pendingAction();
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result, unmount } = renderHookWithQuery(() => useActionMutation(action));
    await startRun(result, { onSuccess, onError });
    unmount();
    await act(async () => {
      rejectAction(new Error("late"));
      await Promise.resolve();
    });
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });
});
