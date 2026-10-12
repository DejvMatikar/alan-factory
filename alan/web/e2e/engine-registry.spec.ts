import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures/test-base";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

test(
  "register an engine via the API and dispatch a workspace pinned to it",
  { tag: "@no-claude" },
  async ({ request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const owner = "e2e-engine-co";
    const repo = `widget-${String(Date.now())}`;
    const slug = `${owner}/${repo}`;

    const unresolvedRes = await request.post("/api/workspaces", {
      data: { title: "E2E unresolved engine", engine_key: slug },
    });
    expect(unresolvedRes.status()).toBe(400);

    const noTokenRes = await request.post("/api/engines", {
      data: { repo: { owner, repo } },
    });
    expect(noTokenRes.status()).toBe(401);

    const createRes = await request.post("/api/engines", {
      data: { repo: { owner, repo } },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(createRes.status()).toBe(201);
    const createBody = (await createRes.json()) as { repo: { owner: string; repo: string } };
    expect(createBody.repo).toEqual({ owner, repo });

    const listRes = await request.get("/api/engines");
    expect(listRes.status()).toBe(200);
    const listBody = (await listRes.json()) as {
      engines: { repo: { owner: string; repo: string } }[];
    };
    expect(
      listBody.engines.some((e) => e.repo.owner === owner && e.repo.repo === repo),
    ).toBe(true);

    const detailRes = await request.get(`/api/engines/${owner}/${repo}`);
    expect(detailRes.status()).toBe(200);

    const dispatchRes = await request.post("/api/workspaces", {
      data: {
        title: `E2E Engine Dispatch ${String(Date.now())}`,
        description: "pinned to a registered engine",
        engine_key: slug,
      },
    });
    expect(dispatchRes.status()).toBe(201);
    const dispatchBody = (await dispatchRes.json()) as { task_id: string };
    expect(dispatchBody.task_id).toBeTruthy();

    const noTokenUpdateRes = await request.patch(`/api/engines/${owner}/${repo}`, {
      data: { repo: { owner, repo }, posture: "propose" },
    });
    expect(noTokenUpdateRes.status()).toBe(401);

    const updateRes = await request.patch(`/api/engines/${owner}/${repo}`, {
      data: { repo: { owner, repo }, posture: "propose" },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(updateRes.status()).toBe(200);
    const updateBody = (await updateRes.json()) as { posture: string };
    expect(updateBody.posture).toBe("observe");

    const noTokenPostureRes = await request.post(`/api/engines/${owner}/${repo}/posture`, {
      data: { posture: "propose", confirm: true },
    });
    expect(noTokenPostureRes.status()).toBe(401);

    const postureRes = await request.post(`/api/engines/${owner}/${repo}/posture`, {
      data: { posture: "propose", confirm: true },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(postureRes.status()).toBe(200);
    const postureBody = (await postureRes.json()) as { posture: string };
    expect(postureBody.posture).toBe("propose");

    const updatedDetailRes = await request.get(`/api/engines/${owner}/${repo}`);
    const updatedDetailBody = (await updatedDetailRes.json()) as { posture: string };
    expect(updatedDetailBody.posture).toBe("propose");

    const noTokenDeleteRes = await request.delete(`/api/engines/${owner}/${repo}`);
    expect(noTokenDeleteRes.status()).toBe(401);

    const blockedDeleteRes = await request.delete(`/api/engines/${owner}/${repo}`, {
      headers: { "X-Alan-Control-Token": token },
    });
    expect(blockedDeleteRes.status()).toBe(409);
    const blockedDeleteBody = (await blockedDeleteRes.json()) as {
      detail: { blocking_workspace_ids: string[] };
    };
    expect(blockedDeleteBody.detail.blocking_workspace_ids).toContain(dispatchBody.task_id);

    const deleteRes = await request.delete(`/api/engines/${owner}/${repo}`, {
      params: { force: "true" },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(deleteRes.status()).toBe(204);

    const goneRes = await request.get(`/api/engines/${owner}/${repo}`);
    expect(goneRes.status()).toBe(404);
  },
);

test(
  "lists the stack-template preset catalog without requiring a control token, for the roster preset-seed UI",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.get("/api/engine-presets");
    expect(res.status()).toBe(200);
    const body = (await res.json()) as { presets: { name: string }[] };
    expect(body.presets.map((p) => p.name).sort()).toEqual(["dotnet", "ios", "node", "python"]);
  },
);

test(
  "excluding a team from the roster via PATCH persists a transition_graph that omits it, leaving every other team's edges unchanged",
  { tag: "@no-claude" },
  async ({ request, e2eRoot }) => {
    const token = readControlToken(e2eRoot);
    const owner = "e2e-engine-roster-co";
    const repo = `widget-${String(Date.now())}`;

    const createRes = await request.post("/api/engines", {
      data: {
        repo: { owner, repo },
        transition_graph: {
          research: ["brainstorming"],
          brainstorming: ["research"],
          opportunity_scout: ["research", "brainstorming"],
        },
      },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(createRes.status()).toBe(201);

    const updateRes = await request.patch(`/api/engines/${owner}/${repo}`, {
      data: {
        repo: { owner, repo },
        transition_graph: { research: ["brainstorming"], brainstorming: ["research"] },
      },
      headers: { "X-Alan-Control-Token": token },
    });
    expect(updateRes.status()).toBe(200);
    const updateBody = (await updateRes.json()) as {
      transition_graph: Record<string, string[]>;
    };
    expect(updateBody.transition_graph).toEqual({
      research: ["brainstorming"],
      brainstorming: ["research"],
    });
    expect(updateBody.transition_graph.opportunity_scout).toBeUndefined();

    const detailRes = await request.get(`/api/engines/${owner}/${repo}`);
    const detailBody = (await detailRes.json()) as {
      transition_graph: Record<string, string[]>;
    };
    expect(detailBody.transition_graph).toEqual({
      research: ["brainstorming"],
      brainstorming: ["research"],
    });
  },
);
