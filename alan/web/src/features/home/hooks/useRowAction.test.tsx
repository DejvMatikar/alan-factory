import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../../api";
import { useRowAction } from "./useRowAction";

describe("useRowAction — dispatch by status", () => {
  it("dispatches stopWorkspace for an active workspace", async () => {
    const stop = vi.fn().mockResolvedValue(undefined);
    const original = api.stopWorkspace;
    api.stopWorkspace = stop;
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w1", "active", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(stop).toHaveBeenCalledWith("w1");
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      api.stopWorkspace = original;
    }
  });

  it("dispatches archiveWorkspace for a completed workspace", async () => {
    const archive = vi.fn().mockResolvedValue(undefined);
    const original = api.archiveWorkspace;
    api.archiveWorkspace = archive;
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w2", "completed", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(archive).toHaveBeenCalledWith("w2");
    } finally {
      api.archiveWorkspace = original;
    }
  });

  it("dispatches unarchiveWorkspace for an archived workspace", async () => {
    const unarchive = vi.fn().mockResolvedValue(undefined);
    const original = api.unarchiveWorkspace;
    api.unarchiveWorkspace = unarchive;
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w3", "archived", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(unarchive).toHaveBeenCalledWith("w3");
    } finally {
      api.unarchiveWorkspace = original;
    }
  });
});

describe("useRowAction — error handling", () => {
  it("treats a 404 as a benign race, clearing busy with no error and still reloading", async () => {
    const original = api.stopWorkspace;
    api.stopWorkspace = vi.fn().mockRejectedValue(new ApiError(404, "gone"));
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w4", "active", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(result.current.error).toBeNull();
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      api.stopWorkspace = original;
    }
  });

  it("treats a 409 as a benign race, clearing busy with no error", async () => {
    const original = api.archiveWorkspace;
    api.archiveWorkspace = vi.fn().mockRejectedValue(new ApiError(409, "conflict"));
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w5", "completed", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(result.current.error).toBeNull();
    } finally {
      api.archiveWorkspace = original;
    }
  });

  it("surfaces a genuine failure's message and still reloads", async () => {
    const original = api.stopWorkspace;
    api.stopWorkspace = vi.fn().mockRejectedValue(new Error("boom"));
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w6", "active", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(result.current.error).toBe("boom");
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      api.stopWorkspace = original;
    }
  });

  it("stringifies a non-Error rejection as the surfaced error message", async () => {
    const original = api.stopWorkspace;
    api.stopWorkspace = vi.fn().mockRejectedValue("string-failure");
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w8", "active", reload));
      act(() => {
        result.current.run();
      });
      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(result.current.error).toBe("string-failure");
    } finally {
      api.stopWorkspace = original;
    }
  });

  it("sets busy synchronously while the request is in flight", () => {
    const original = api.stopWorkspace;
    let resolveGate!: () => void;
    api.stopWorkspace = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveGate = resolve;
        }),
    );
    const reload = vi.fn();
    try {
      const { result } = renderHook(() => useRowAction("w7", "active", reload));
      act(() => {
        result.current.run();
      });
      expect(result.current.busy).toBe(true);
      resolveGate();
    } finally {
      api.stopWorkspace = original;
    }
  });
});
