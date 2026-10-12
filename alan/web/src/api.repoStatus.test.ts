import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, expectTypeOf, it } from "vitest";

import {
  ApiError,
  api,
  type RepoStatus,
  type RepoStatusDeployOutcome,
  type RepoStatusHealthVerdict,
  type RepoStatusProvenanceStatus,
  type WorkspaceSizeClass,
} from "./api";
import type { components } from "./api/schema";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const STATUS_ROUTE = "/api/engines/:owner/:repo/status";
const ENGINE_KEY = "acme.io/widget_shop-2";

const FRESH = { status: "fresh", detail: "read just now" } as const;

const WIRE_STATUS = {
  slug: ENGINE_KEY,
  is_default: false,
  main_ci: { provenance: FRESH, green_count: 41, red_count: 2, counted_over: "main_ci_workflow" },
  open_prs: { provenance: FRESH, total: 5, authored_by_alan: 3 },
  cost: { provenance: FRESH, total_cost_usd: 12.5, unknown_count: 1 },
  delivery_time: {
    provenance: FRESH,
    by_size: [{ size: "M", median_active_seconds: 5400, delivered_count: 7 }],
  },
  last_deploy: { provenance: FRESH, outcome: "confirmed", pr_number: 1679, at: "2026-09-19T08:00:00Z" },
  health: { provenance: FRESH, finding_count: 0, verdict: "healthy" },
} satisfies RepoStatus;

const NO_DATA_STATUS = {
  slug: ENGINE_KEY,
  is_default: false,
  main_ci: { provenance: FRESH, green_count: null, red_count: null, counted_over: null },
  open_prs: { provenance: FRESH, total: null, authored_by_alan: null },
  cost: { provenance: FRESH, total_cost_usd: null, unknown_count: 0 },
  delivery_time: { provenance: FRESH, by_size: [] },
  last_deploy: { provenance: FRESH, outcome: null, pr_number: null, at: null },
  health: { provenance: FRESH, finding_count: null, verdict: null },
} satisfies RepoStatus;

function watchStatusRoute(respond: () => Response) {
  const hits: Request[] = [];
  server.use(
    http.get(STATUS_ROUTE, ({ request }) => {
      hits.push(request);
      return respond();
    }),
  );
  return hits;
}

beforeEach(() => {
  clearControlToken();
});

describe("getRepoStatus request", () => {
  it("reaches the status route with the owner and repo segments passed through literally", async () => {
    const hits = watchStatusRoute(() => HttpResponse.json(WIRE_STATUS));
    await api.getRepoStatus(ENGINE_KEY);
    const [only] = hits;
    expect(hits).toHaveLength(1);
    expect(only?.method).toBe("GET");
    expect(new URL(only?.url ?? "").pathname).toBe("/api/engines/acme.io/widget_shop-2/status");
  });

  it("carries no control token on a same-origin read even when one is stored", async () => {
    writeControlToken("stored-token");
    const hits = watchStatusRoute(() => HttpResponse.json(WIRE_STATUS));
    await api.getRepoStatus(ENGINE_KEY);
    expect(hits[0]?.headers.get(CONTROL_TOKEN_HEADER)).toBeNull();
  });

  it.each([
    ["an owner with a space", "bad owner/repo", "invalid engine_owner: bad owner"],
    ["a repo with a path separator", "acme/evil/repo", "invalid engine_repo: evil/repo"],
    ["a key with no slash", "loneowner", "invalid engine_owner: "],
    ["a repo with a query character", "acme/repo?x=1", "invalid engine_repo: repo?x=1"],
  ])("rejects %s before any request is issued", async (_label, key, message) => {
    const hits = watchStatusRoute(() => HttpResponse.json(WIRE_STATUS));
    await expect(api.getRepoStatus(key)).rejects.toThrow(message);
    expect(hits).toHaveLength(0);
  });
});

describe("getRepoStatus response", () => {
  it("resolves to the parsed body of a fully populated status", async () => {
    watchStatusRoute(() => HttpResponse.json(WIRE_STATUS));
    await expect(api.getRepoStatus(ENGINE_KEY)).resolves.toEqual(WIRE_STATUS);
  });

  it("resolves to the parsed body when every field reports no data as null", async () => {
    watchStatusRoute(() => HttpResponse.json(NO_DATA_STATUS));
    const status = await api.getRepoStatus(ENGINE_KEY);
    expect(status.main_ci.green_count).toBeNull();
    expect(status.cost.total_cost_usd).toBeNull();
    expect(status.cost.unknown_count).toBe(0);
    expect(status.delivery_time.by_size).toEqual([]);
    expect(status.health.verdict).toBeNull();
  });

  it("surfaces a 404 detail through ApiError", async () => {
    watchStatusRoute(() => HttpResponse.json({ detail: "engine not found" }, { status: 404 }));
    const failure = await api.getRepoStatus(ENGINE_KEY).catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 404, detail: "engine not found" });
    expect((failure as ApiError).message).toBe("404: engine not found");
  });

  it("falls back to the status text when the error body has no detail", async () => {
    watchStatusRoute(() => HttpResponse.json({}, { status: 502, statusText: "Bad Gateway" }));
    await expect(api.getRepoStatus(ENGINE_KEY)).rejects.toMatchObject({
      status: 502,
      detail: "Bad Gateway",
    });
  });
});

describe("repo-status generated types", () => {
  it("keeps null and undefined out of the narrowed deploy outcome and health verdict", () => {
    expectTypeOf<RepoStatusDeployOutcome>().toEqualTypeOf<"confirmed" | "escalate">();
    expectTypeOf<RepoStatusHealthVerdict>().toEqualTypeOf<"healthy" | "degraded">();
    expectTypeOf<null>().not.toExtend<RepoStatusDeployOutcome>();
    expectTypeOf<undefined>().not.toExtend<RepoStatusHealthVerdict>();
  });

  it("resolves the size class and provenance status to the generated unions", () => {
    expectTypeOf<WorkspaceSizeClass>().toEqualTypeOf<components["schemas"]["SizeClass"]>();
    expectTypeOf<RepoStatusProvenanceStatus>().toEqualTypeOf<
      "fresh" | "stale" | "refreshing" | "unknown"
    >();
  });

  it("admits no undefined key in a verdict-keyed record", () => {
    expectTypeOf<keyof Record<RepoStatusHealthVerdict, string>>().toEqualTypeOf<
      "healthy" | "degraded"
    >();
    expectTypeOf<Record<RepoStatusHealthVerdict, string>>().toEqualTypeOf<{
      healthy: string;
      degraded: string;
    }>();
  });
});
