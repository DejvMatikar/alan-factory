import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useDeletableDraft } from "./useDeletableDraft";

function pendingDelete() {
  let resolveDelete!: () => void;
  let rejectDelete!: (err: unknown) => void;
  const deleteCall = vi.fn(
    () =>
      new Promise<void>((resolve, reject) => {
        resolveDelete = resolve;
        rejectDelete = reject;
      }),
  );
  return {
    deleteCall,
    resolveDelete: () => resolveDelete(),
    rejectDelete: (err: unknown) => rejectDelete(err),
  };
}

async function startDelete(result: { current: { handleDelete: () => void } }) {
  await act(async () => {
    result.current.handleDelete();
    await Promise.resolve();
  });
}

describe("useDeletableDraft", () => {
  it("starts idle with no error", () => {
    const { result } = renderHook(() => useDeletableDraft(() => Promise.resolve(), vi.fn()));
    expect(result.current.deleting).toBe(false);
    expect(result.current.deleteError).toBeNull();
  });

  it("is busy once handleDelete is called and the delete is in flight", async () => {
    const { deleteCall, resolveDelete } = pendingDelete();
    const { result } = renderHook(() => useDeletableDraft(deleteCall, vi.fn()));
    await startDelete(result);
    expect(result.current.deleting).toBe(true);
    await act(async () => {
      resolveDelete();
      await Promise.resolve();
    });
  });

  it("calls onDeleted on success and stays busy, since the caller removes this draft", async () => {
    const onDeleted = vi.fn();
    const { result } = renderHook(() => useDeletableDraft(() => Promise.resolve(), onDeleted));
    act(() => {
      result.current.handleDelete();
    });
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
    expect(result.current.deleting).toBe(true);
    expect(result.current.deleteError).toBeNull();
  });

  it("surfaces the error and clears deleting when the delete call rejects", async () => {
    const onDeleted = vi.fn();
    const { result } = renderHook(() =>
      useDeletableDraft(() => Promise.reject(new Error("gone")), onDeleted),
    );
    act(() => {
      result.current.handleDelete();
    });
    await waitFor(() => expect(result.current.deleteError).toBe("gone"));
    expect(result.current.deleting).toBe(false);
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("stringifies a non-Error rejection", async () => {
    const { result } = renderHook(() =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      useDeletableDraft(() => Promise.reject("plain-failure"), vi.fn()),
    );
    act(() => {
      result.current.handleDelete();
    });
    await waitFor(() => expect(result.current.deleteError).toBe("plain-failure"));
  });

  it("clears the previous error when a retry starts and lets the retry succeed", async () => {
    const onDeleted = vi.fn();
    const deleteCall = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new Error("first try"))
      .mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => useDeletableDraft(deleteCall, onDeleted));
    act(() => {
      result.current.handleDelete();
    });
    await waitFor(() => expect(result.current.deleteError).toBe("first try"));
    act(() => {
      result.current.handleDelete();
    });
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
    expect(result.current.deleteError).toBeNull();
  });

  it("does not report a delete that succeeds after unmount", async () => {
    const { deleteCall, resolveDelete } = pendingDelete();
    const onDeleted = vi.fn();
    const { result, unmount } = renderHook(() => useDeletableDraft(deleteCall, onDeleted));
    await startDelete(result);
    unmount();
    await act(async () => {
      resolveDelete();
      await Promise.resolve();
    });
    expect(onDeleted).not.toHaveBeenCalled();
    expect(result.current.deleteError).toBeNull();
  });

  it("does not report a delete that fails after unmount", async () => {
    const { deleteCall, rejectDelete } = pendingDelete();
    const onDeleted = vi.fn();
    const { result, unmount } = renderHook(() => useDeletableDraft(deleteCall, onDeleted));
    await startDelete(result);
    unmount();
    await act(async () => {
      rejectDelete(new Error("too late"));
      await Promise.resolve();
    });
    expect(onDeleted).not.toHaveBeenCalled();
    expect(result.current.deleteError).toBeNull();
  });
});
