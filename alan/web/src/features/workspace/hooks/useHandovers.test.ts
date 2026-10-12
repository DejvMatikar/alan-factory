import { waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../../api";
import { makeHandover } from "../../../test/workspaceFixtures";
import { renderHookWithQuery } from "../../../test/renderWithQuery";
import { server } from "../../../test/server";
import { findHandoverFrom, resolveHandoverTargetLabel, useHandovers } from "./useHandovers";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useHandovers", () => {
  it("returns an empty list while loading", () => {
    server.use(
      http.get(
        "/api/workspaces/t1/handovers",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { result } = renderHookWithQuery(() => useHandovers("t1", ["inv_001"]));
    expect(result.current.handovers).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("populates handovers after a successful fetch", async () => {
    const handover = makeHandover({ handover_id: "h1" });
    server.use(
      http.get("/api/workspaces/t1/handovers", () => HttpResponse.json([handover])),
    );
    const { result } = renderHookWithQuery(() => useHandovers("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.handovers).toEqual([handover]));
    expect(result.current.error).toBeNull();
  });

  it("surfaces an error message on fetch failure", async () => {
    server.use(
      http.get("/api/workspaces/t1/handovers", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );
    const { result } = renderHookWithQuery(() => useHandovers("t1", ["inv_001"]));
    await waitFor(() => expect(result.current.error).toContain("404"));
    expect(result.current.handovers).toEqual([]);
  });

  it("re-reads when the invocation list changes", async () => {
    const getHandovers = vi.spyOn(api, "getHandovers").mockResolvedValue([]);
    const { rerender } = renderHookWithQuery(
      ({ invocations }: { invocations: readonly string[] }) => useHandovers("t1", invocations),
      { initialProps: { invocations: ["inv_001"] } },
    );
    await waitFor(() => expect(getHandovers).toHaveBeenCalledTimes(1));

    rerender({ invocations: ["inv_001", "inv_002"] });
    await waitFor(() => expect(getHandovers).toHaveBeenCalledTimes(2));
  });

  it("keeps caches separate per task", async () => {
    vi.spyOn(api, "getHandovers").mockImplementation((taskId) =>
      Promise.resolve([makeHandover({ handover_id: `handover_${taskId}` })]),
    );
    const { result, rerender } = renderHookWithQuery(
      ({ taskId }: { taskId: string }) => useHandovers(taskId, ["inv_001"]),
      { initialProps: { taskId: "t1" } },
    );
    await waitFor(() => expect(result.current.handovers[0]?.handover_id).toBe("handover_t1"));

    rerender({ taskId: "t2" });
    await waitFor(() => expect(result.current.handovers[0]?.handover_id).toBe("handover_t2"));
  });
});

describe("resolveHandoverTargetLabel", () => {
  it("reads 'Completed' for a terminal handover", () => {
    expect(resolveHandoverTargetLabel(makeHandover({ handover_type: "terminal" }))).toBe(
      "Completed",
    );
  });

  it("reads 'Escalated' for an escalation handover", () => {
    expect(resolveHandoverTargetLabel(makeHandover({ handover_type: "escalation" }))).toBe(
      "Escalated",
    );
  });

  it("reads the first suggested team for a forward handover", () => {
    expect(
      resolveHandoverTargetLabel(
        makeHandover({ handover_type: "forward", to_team_suggestions: ["verification", "research"] }),
      ),
    ).toBe("verification");
  });

  it("falls back to await_human when no team is suggested", () => {
    expect(
      resolveHandoverTargetLabel(makeHandover({ handover_type: "forward", to_team_suggestions: [] })),
    ).toBe("await_human");
  });
});

describe("findHandoverFrom", () => {
  it("finds the handover whose from_invocation_id matches", () => {
    const match = makeHandover({ handover_id: "h1", from_invocation_id: "inv_001" });
    const other = makeHandover({ handover_id: "h2", from_invocation_id: "inv_002" });
    expect(findHandoverFrom([other, match], "inv_001")).toBe(match);
  });

  it("returns null when nothing matches", () => {
    const other = makeHandover({ handover_id: "h2", from_invocation_id: "inv_002" });
    expect(findHandoverFrom([other], "inv_001")).toBeNull();
  });
});
