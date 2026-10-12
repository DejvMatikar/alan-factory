import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError, api, type CreateAndDispatchWorkspaceOptions } from "./api";
import { CONTROL_TOKEN_HEADER, clearControlToken, writeControlToken } from "./controlToken";
import { server } from "./test/server";

const TOKEN = "proposal-token";
const SID = "12345678-1234-1234-1234-1234567890ab";
const COUNTER_CASE = { against: "x", cheapest_80: "y", cost_estimate: "insufficient data" };
const FINDING = {
  kind: "sibling_code",
  summary: "a sibling already does this",
  references: ["alan/web/src/api.ts"],
  suggested_action: "expand_scope",
};
const MATCH = { label: "PR #12", kind: "pr", deep_link: "https://example.test/pull/12" };

const DOORS = [
  { label: "build", path: `/api/build-chat/sessions/${SID}/propose`, propose: api.proposeBuildDispatch },
  { label: "explore", path: `/api/explore-chat/sessions/${SID}/propose`, propose: api.proposeExploreDispatch },
] as const;

interface ReadyOverrides {
  proposal?: Record<string, unknown>;
  collision?: Record<string, unknown>;
}

function readyBody(overrides: ReadyOverrides = {}) {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "brief is ready",
      title: "add-thing",
      acceptance_clauses: ["a clause"],
      instructions: null,
      scope_audit: [],
      counter_case: COUNTER_CASE,
      ...overrides.proposal,
    },
    collision: { state: "green", sentence: "No overlap.", match: null, ...overrides.collision },
  };
}

function serve(path: string, respond: () => Response) {
  const seen: { token: string | null; body: string }[] = [];
  server.use(
    http.post(path, async ({ request }) => {
      seen.push({ token: request.headers.get(CONTROL_TOKEN_HEADER), body: await request.text() });
      return respond();
    }),
  );
  return seen;
}

async function rejection(call: () => Promise<unknown>): Promise<ApiError> {
  try {
    await call();
  } catch (err) {
    if (err instanceof ApiError) return err;
  }
  throw new Error("expected an ApiError rejection");
}

beforeEach(() => {
  clearControlToken();
  writeControlToken(TOKEN);
});

describe.each(DOORS)("$label dispatch proposal", ({ path, propose }) => {
  it("passes a ready proposal through with its instructions, scope audit and collision verdict", async () => {
    serve(
      path,
      () =>
        HttpResponse.json(
          readyBody({
            proposal: { instructions: "Focus here.", scope_audit: [FINDING] },
            collision: { state: "red", sentence: "Overlaps PR #12.", match: MATCH },
          }),
        ),
    );
    expect(await propose(SID)).toEqual({
      status: "ready",
      proposal: {
        team: "implementation_plan",
        rationale: "brief is ready",
        title: "add-thing",
        acceptance_clauses: ["a clause"],
        instructions: "Focus here.",
        scope_audit: [FINDING],
        counter_case: COUNTER_CASE,
      },
      collision: { state: "red", sentence: "Overlaps PR #12.", match: MATCH },
    });
  });

  it("keeps null instructions null and a null collision match null", async () => {
    serve(path, () => HttpResponse.json(readyBody()));
    const res = await propose(SID);
    expect(res).toMatchObject({
      status: "ready",
      proposal: { instructions: null, scope_audit: [] },
      collision: { state: "green", sentence: "No overlap.", match: null },
    });
  });

  it("returns the questions of a need_more_info response", async () => {
    serve(path, () => HttpResponse.json({ status: "need_more_info", questions: ["which scope?"] }));
    expect(await propose(SID)).toEqual({ status: "need_more_info", questions: ["which scope?"] });
  });

  it("falls back to need_more_info for a response with no proposal, whatever its status string", async () => {
    serve(path, () => HttpResponse.json({ status: "mystery", questions: ["still unsure?"] }));
    expect(await propose(SID)).toEqual({ status: "need_more_info", questions: ["still unsure?"] });
  });

  it("POSTs with the control token and no request body", async () => {
    const seen = serve(path, () => HttpResponse.json({ status: "need_more_info", questions: [] }));
    await propose(SID);
    expect(seen).toEqual([{ token: TOKEN, body: "" }]);
  });

  it("surfaces a structured 422 detail as code and reason", async () => {
    serve(path, () =>
      HttpResponse.json(
        { detail: { detail: "proposal_parse_failed", reason: "scope_audit issue" } },
        { status: 422 },
      ),
    );
    const err = await rejection(() => propose(SID));
    expect(err.status).toBe(422);
    expect(err.message).toBe("422: proposal_parse_failed: scope_audit issue");
  });

  it("surfaces a string detail on a 409", async () => {
    serve(path, () => HttpResponse.json({ detail: "chat turn already in progress" }, { status: 409 }));
    const err = await rejection(() => propose(SID));
    expect(err.message).toBe("409: chat turn already in progress");
  });

  it("falls back to the status text when the error body is empty", async () => {
    serve(path, () => HttpResponse.text("", { status: 502, statusText: "Bad Gateway" }));
    const err = await rejection(() => propose(SID));
    expect(err.message).toBe("502: Bad Gateway");
  });

  it("passes a proposal's recorded clauses through untouched", async () => {
    serve(path, () =>
      HttpResponse.json(readyBody({ proposal: { recorded_clauses: ["the docs say X"] } })),
    );
    expect(await propose(SID)).toMatchObject({
      status: "ready",
      proposal: { acceptance_clauses: ["a clause"], recorded_clauses: ["the docs say X"] },
    });
  });
});

const WORKSPACES_PATH = "/api/workspaces";

async function requirementsSentFor(options: CreateAndDispatchWorkspaceOptions): Promise<unknown> {
  const seen = serve(WORKSPACES_PATH, () =>
    HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 }),
  );
  await api.createAndDispatchWorkspace("T", "D", "implementation_plan", {}, options);
  return (JSON.parse(seen[0]?.body ?? "") as { requirements?: unknown }).requirements;
}

describe("create-and-dispatch clause kinds", () => {
  it("sends a recorded clause with the record kind", async () => {
    expect(await requirementsSentFor({ recordedClauses: ["the docs say X"] })).toEqual([
      { text: "the docs say X", kind: "record" },
    ]);
  });

  it("sends a blocking clause with no kind key at all", async () => {
    const requirements = await requirementsSentFor({ acceptanceClauses: ["it blocks"] });
    expect(requirements).toEqual([{ text: "it blocks" }]);
    expect((requirements as object[])[0]).not.toHaveProperty("kind");
  });

  it("sends both groups blocking-first without de-duplicating a shared text", async () => {
    expect(
      await requirementsSentFor({ acceptanceClauses: ["shared"], recordedClauses: ["shared"] }),
    ).toEqual([{ text: "shared" }, { text: "shared", kind: "record" }]);
  });

  it("trims recorded clauses and drops blank ones", async () => {
    expect(
      await requirementsSentFor({ recordedClauses: ["  the docs say X  ", "   "] }),
    ).toEqual([{ text: "the docs say X", kind: "record" }]);
  });

  it("omits requirements when both groups are empty", async () => {
    expect(
      await requirementsSentFor({ acceptanceClauses: [], recordedClauses: [] }),
    ).toBeUndefined();
  });

  it("lets an explicit requirements argument override the derivation on createWorkspace", async () => {
    const seen = serve(WORKSPACES_PATH, () =>
      HttpResponse.json({ task_id: "t", root_task_ids: ["t"] }, { status: 201 }),
    );
    const explicit = {
      id: "req-explicit",
      text: "explicit",
      test_ref: null,
      status: "PENDING" as const,
      merged_pr_url: null,
      withdrawn_by_question_id: null,
    };
    await api.createWorkspace("T", "D", undefined, undefined, [explicit], ["derived"]);
    expect((JSON.parse(seen[0]?.body ?? "") as { requirements: unknown }).requirements).toEqual([
      explicit,
    ]);
  });
});
