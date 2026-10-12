import { act, render, renderHook, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { useUrlDraftSession } from "./useUrlDraftSession";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const NEW_SID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

function mockCreate(sid = SID): () => Promise<{ session_id: string }> {
  return () => Promise.resolve({ session_id: sid });
}

function makeWrapper(initialEntries: string[]) {
  return function Wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
    return (
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="*" element={<>{children}</>} />
        </Routes>
      </MemoryRouter>
    );
  };
}

describe("useUrlDraftSession", () => {
  it("uses session_id from ?s= param immediately", () => {
    const { result } = renderHook(
      () => useUrlDraftSession(mockCreate()),
      { wrapper: makeWrapper([`/chat?s=${SID}`]) },
    );
    expect(result.current.sessionId).toBe(SID);
    expect(result.current.bootstrapError).toBeNull();
  });

  it("creates new session when no ?s= param", async () => {
    const { result } = renderHook(
      () => useUrlDraftSession(mockCreate(NEW_SID)),
      { wrapper: makeWrapper(["/chat"]) },
    );
    await waitFor(() => expect(result.current.sessionId).toBe(NEW_SID));
    expect(result.current.bootstrapError).toBeNull();
  });

  it("emits exactly [null, id] across the back-fill", async () => {
    const seen: (string | null)[] = [];
    const { result } = renderHook(
      () => {
        const { sessionId } = useUrlDraftSession(mockCreate(NEW_SID));
        seen.push(sessionId);
        return sessionId;
      },
      { wrapper: makeWrapper(["/chat"]) },
    );
    await waitFor(() => expect(result.current).toBe(NEW_SID));
    const distinct = seen.filter((v, i) => i === 0 || v !== seen[i - 1]);
    expect(distinct).toEqual([null, NEW_SID]);
  });

  it("does not spawn a second session when query params churn mid-create", async () => {
    let resolveCreate!: (value: { session_id: string }) => void;
    const create = vi.fn(
      () => new Promise<{ session_id: string }>((res) => { resolveCreate = res; }),
    );

    function Probe() {
      const [params, setParams] = useSearchParams();
      const { sessionId } = useUrlDraftSession(create);
      useEffect(() => {
        if (!params.has("x")) setParams({ x: "1" }, { replace: true });
      }, [params, setParams]);
      return <div data-testid="sid">{sessionId ?? ""}</div>;
    }

    render(<Probe />, { wrapper: makeWrapper(["/build"]) });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await act(async () => {
      resolveCreate({ session_id: NEW_SID });
      await Promise.resolve();
    });
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("abandons a stale in-flight create when the create fn changes", async () => {
    let resolveStale!: (value: { session_id: string }) => void;
    const createA = vi.fn(
      () => new Promise<{ session_id: string }>((res) => { resolveStale = res; }),
    );
    const createB = vi.fn(() => Promise.resolve({ session_id: NEW_SID }));

    const { rerender, result } = renderHook(
      ({ c }: { c: () => Promise<{ session_id: string }> }) => useUrlDraftSession(c),
      { wrapper: makeWrapper(["/explore/a"]), initialProps: { c: createA } },
    );
    expect(createA).toHaveBeenCalledTimes(1);

    rerender({ c: createB });
    await waitFor(() => expect(result.current.sessionId).toBe(NEW_SID));
    await act(async () => {
      resolveStale({ session_id: "stale-session" });
      await Promise.resolve();
    });
    expect(result.current.sessionId).toBe(NEW_SID);
  });

  it("does not create session when enabled=false", async () => {
    const create = vi.fn().mockResolvedValue({ session_id: SID });
    renderHook(
      () => useUrlDraftSession(create, false),
      { wrapper: makeWrapper(["/chat"]) },
    );
    await act(async () => { await Promise.resolve(); });
    expect(create).not.toHaveBeenCalled();
  });

  it("sets bootstrap error when create fails", async () => {
    const failCreate = () => Promise.reject(new Error("network-fail"));
    const { result } = renderHook(
      () => useUrlDraftSession(failCreate),
      { wrapper: makeWrapper(["/chat"]) },
    );
    await waitFor(() => expect(result.current.bootstrapError).toBe("network-fail"));
  });

  it("preserves existing query params when back-filling ?s=", async () => {
    function Probe({ create }: Readonly<{ create: () => Promise<{ session_id: string }> }>) {
      const { sessionId } = useUrlDraftSession(create);
      const { search } = useLocation();
      return <div data-testid="loc">{sessionId === null ? "" : search}</div>;
    }
    const { getByTestId } = render(<Probe create={mockCreate(NEW_SID)} />, {
      wrapper: makeWrapper(["/build?description=hello%20world"]),
    });
    await waitFor(() => {
      const params = new URLSearchParams(getByTestId("loc").textContent ?? "");
      expect(params.get("s")).toBe(NEW_SID);
      expect(params.get("description")).toBe("hello world");
    });
  });

  it("sets non-Error bootstrap error as string", async () => {
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
    const failCreate = () => Promise.reject("string-fail");
    const { result } = renderHook(
      () => useUrlDraftSession(failCreate),
      { wrapper: makeWrapper(["/chat"]) },
    );
    await waitFor(() => expect(result.current.bootstrapError).toBe("string-fail"));
  });
});
