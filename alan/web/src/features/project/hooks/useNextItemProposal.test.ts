import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import { useNextItemProposal } from "./useNextItemProposal";

const PROPOSAL = {
  fingerprint: null,
  workspace_id: null,
  task_id: null,
  project_id: "proj-1",
  engine_key: "DejvMatikar/alan",
  title: "Next: Ship the widget",
  prepared_context: "Build the widget.",
  recommendation: "All prior roadmap items have delivered — start it next.",
  actions: [
    {
      kind: "direct_dispatch",
      label: "Start: Ship the widget",
      team: null,
      instructions: null,
      url: "/api/projects/proj-1/roadmap/advance",
    },
  ],
  updated_at: "2026-07-08T00:00:00Z",
  autonomy: "propose",
};

describe("useNextItemProposal", () => {
  it("fetches the proposal and budget state when enabled", async () => {
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () =>
        HttpResponse.json({ proposal: PROPOSAL, budget_state: "normal" }),
      ),
    );
    const { result } = renderHook(() => useNextItemProposal("proj-1", true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.proposal?.title).toBe("Next: Ship the widget");
    expect(result.current.budgetState).toBe("normal");
    expect(result.current.error).toBe(false);
  });

  it("issues no fetch and stays unloaded when disabled", () => {
    let calls = 0;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        calls += 1;
        return HttpResponse.json({ proposal: null, budget_state: "normal" });
      }),
    );
    const { result } = renderHook(() => useNextItemProposal("proj-1", false));
    expect(result.current.loaded).toBe(false);
    expect(calls).toBe(0);
  });

  it("fetches once re-enabled after starting disabled", async () => {
    let calls = 0;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        calls += 1;
        return HttpResponse.json({ proposal: null, budget_state: "normal" });
      }),
    );
    const { result, rerender } = renderHook(
      ({ enabled }) => useNextItemProposal("proj-1", enabled),
      { initialProps: { enabled: false } },
    );
    expect(calls).toBe(0);
    rerender({ enabled: true });
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(calls).toBe(1);
  });

  it("tolerates a network error and reports it", async () => {
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => HttpResponse.error()),
    );
    const { result } = renderHook(() => useNextItemProposal("proj-1", true));
    await waitFor(() => {
      expect(result.current.loaded).toBe(true);
    });
    expect(result.current.error).toBe(true);
    expect(result.current.proposal).toBeNull();
  });

  it("advance() POSTs and re-fetches on success, without an optimistic mutation", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        getCalls += 1;
        return HttpResponse.json({
          proposal: getCalls === 1 ? PROPOSAL : null,
          budget_state: "normal",
        });
      }),
      http.post("/api/projects/proj-1/roadmap/advance", () =>
        HttpResponse.json(
          { task_id: "ws-1", item_id: "item-1", item_title: "Item" },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => useNextItemProposal("proj-1", true));
    await waitFor(() => {
      expect(result.current.proposal).not.toBeNull();
    });

    result.current.advance();

    await waitFor(() => {
      expect(result.current.proposal).toBeNull();
    });
    expect(getCalls).toBe(2);
    expect(result.current.advanceError).toBeNull();
  });

  it("advance() surfaces a 409 error and still re-reads the server", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        getCalls += 1;
        return HttpResponse.json({ proposal: PROPOSAL, budget_state: "normal" });
      }),
      http.post("/api/projects/proj-1/roadmap/advance", () =>
        HttpResponse.json(
          { detail: "no un-attempted roadmap item to advance to" },
          { status: 409 },
        ),
      ),
    );
    const { result } = renderHook(() => useNextItemProposal("proj-1", true));
    await waitFor(() => {
      expect(result.current.proposal).not.toBeNull();
    });

    result.current.advance();

    await waitFor(() => {
      expect(result.current.advanceError).not.toBeNull();
    });
    expect(result.current.advanceError).toContain("409");
    expect(getCalls).toBe(2);
  });
});
