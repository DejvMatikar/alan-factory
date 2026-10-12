import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { ApiError, api } from "./api";
import type { UnblockFleetActivity, UnblockScopedActivity } from "./api";
import { server } from "./test/server";

const FLEET: UnblockFleetActivity = {
  scope: "fleet",
  window_days: 30,
  events_total: 4,
  workspaces_touched: 3,
  waited_workspaces: 1,
  diagnosed_workspaces: 1,
  diagnosable_workspaces: 2,
  still_stuck_workspaces: 1,
  gave_up_workspaces: 1,
  aggregates: [
    {
      shape: "merge_park",
      total: 4,
      workspaces: 3,
      cleared_workspaces: 2,
      still_stuck_workspaces: 1,
      gone_workspaces: 0,
      retried_workspaces: 1,
      attempt_outcomes: [{ outcome: "acted", count: 3 }],
      report_outcomes: [{ outcome: "needs_human", count: 1 }],
    },
  ],
  ever_recorded: true,
  unattributed_events: 0,
};

const SCOPED: UnblockScopedActivity = {
  scope: "repo",
  repo: "acme/widget",
  window_days: 30,
  events_total: 1,
  workspaces_touched: 1,
  waited_workspaces: 0,
  diagnosed_workspaces: 0,
  diagnosable_workspaces: 1,
  still_stuck_workspaces: 0,
  gave_up_workspaces: 0,
  aggregates: [],
  fleet_ever_recorded: true,
  events: [
    {
      id: 7,
      at: "2026-09-19T14:05Z",
      workspace_key: "ws-1",
      shape: "merge_park",
      kind: "attempt",
      action: "rebase",
      outcome: "acted",
      reason: null,
      authority_holder: null,
      clearance: "cleared",
      attempt: {
        fingerprint_index: 1,
        fingerprint_ceiling: 2,
        workspace_index: 1,
        workspace_ceiling: 3,
      },
      attempts_total: null,
      issue_url: null,
      diagnosis: null,
    },
  ],
  workspaces: [
    {
      workspace_key: "ws-1",
      last_at: "2026-09-19T14:05Z",
      shape: "merge_park",
      events_total: 3,
      events_listed: 1,
      attempts_total: 6,
      attempts_ceiling: 5,
      clearance: "still_stuck",
      gave_up: true,
      issue_url: "https://github.com/acme/widget/issues/9",
      diagnosis: { signature: "fd-exhaustion", proposed_disposition: "root_caused", verified: true },
    },
  ],
};

describe("api.getUnblockerActivity", () => {
  it("GETs the bare endpoint with no query string for the fleet view", async () => {
    let method = "";
    let search = "unset";
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) => {
        method = request.method;
        search = new URL(request.url).search;
        return HttpResponse.json(FLEET);
      }),
    );
    const resp = await api.getUnblockerActivity(null);
    expect(method).toBe("GET");
    expect(search).toBe("");
    expect(resp).toEqual(FLEET);
  });

  it("sends the engine slug as an encoded query parameter for the scoped view", async () => {
    let rawSearch = "";
    let engine: string | null = null;
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) => {
        const url = new URL(request.url);
        rawSearch = url.search;
        engine = url.searchParams.get("engine");
        return HttpResponse.json(SCOPED);
      }),
    );
    const resp = await api.getUnblockerActivity("acme/widget");
    expect(rawSearch).toBe("?engine=acme%2Fwidget");
    expect(engine).toBe("acme/widget");
    expect(resp).toEqual(SCOPED);
  });

  it("encodes characters that would otherwise split the query string", async () => {
    let engine: string | null = null;
    let paramCount = 0;
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) => {
        const params = new URL(request.url).searchParams;
        engine = params.get("engine");
        paramCount = [...params.keys()].length;
        return HttpResponse.json(SCOPED);
      }),
    );
    await api.getUnblockerActivity("a&b=c d");
    expect(engine).toBe("a&b=c d");
    expect(paramCount).toBe(1);
  });

  it("passes the summary counts through on the fleet view without a workspaces list", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(FLEET)));
    const resp = await api.getUnblockerActivity(null);
    expect(resp.still_stuck_workspaces).toBe(1);
    expect(resp.gave_up_workspaces).toBe(1);
    expect("workspaces" in resp).toBe(false);
  });

  it("passes the per-workspace aggregate through unclamped on the scoped view", async () => {
    server.use(http.get("/api/health/unblocker-activity", () => HttpResponse.json(SCOPED)));
    const resp = await api.getUnblockerActivity("acme/widget");
    const rows = resp.scope === "repo" ? resp.workspaces : undefined;
    expect(rows).toHaveLength(1);
    expect(rows?.[0]).toMatchObject({
      workspace_key: "ws-1",
      events_total: 3,
      events_listed: 1,
      attempts_total: 6,
      attempts_ceiling: 5,
      gave_up: true,
      clearance: "still_stuck",
      issue_url: "https://github.com/acme/widget/issues/9",
    });
    expect(rows?.[0]?.diagnosis?.signature).toBe("fd-exhaustion");
  });

  it("narrows the response on scope for both variants", async () => {
    server.use(
      http.get("/api/health/unblocker-activity", ({ request }) =>
        HttpResponse.json(new URL(request.url).searchParams.has("engine") ? SCOPED : FLEET),
      ),
    );
    const fleet = await api.getUnblockerActivity(null);
    const repo = await api.getUnblockerActivity("acme/widget");
    expect(fleet.scope === "fleet" ? fleet.unattributed_events : null).toBe(0);
    expect(repo.scope === "repo" ? repo.workspaces.length : null).toBe(1);
  });

  it("rejects with an ApiError on a non-2xx response", async () => {
    server.use(
      http.get("/api/health/unblocker-activity", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    let caught: ApiError | null = null;
    try {
      await api.getUnblockerActivity(null);
    } catch (err) {
      if (err instanceof ApiError) caught = err;
    }
    expect(caught?.status).toBe(500);
    expect(caught?.detail).toBe("boom");
  });
});
