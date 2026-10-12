import { expect, test } from "./fixtures/test-base";

test(
  "triage workspace is seeded at startup and hidden from default list",
  { tag: "@no-claude" },
  async ({ request }) => {
    const defaultRes = await request.get("/api/workspaces");
    expect(defaultRes.status()).toBe(200);
    const defaultList = (await defaultRes.json()) as { task_id: string }[];

    const withTriageRes = await request.get("/api/workspaces?include=triage");
    expect(withTriageRes.status()).toBe(200);
    const withTriage = (await withTriageRes.json()) as { task_id: string }[];

    const defaultIds = new Set(defaultList.map((w) => w.task_id));
    const triageOnlyIds = withTriage
      .map((w) => w.task_id)
      .filter((id) => !defaultIds.has(id));

    expect(triageOnlyIds.length).toBeGreaterThanOrEqual(1);

    for (const triageId of triageOnlyIds) {
      const detailRes = await request.get(`/api/workspaces/${triageId}`);
      expect(detailRes.status()).toBe(404);

      const detailWithInclude = await request.get(
        `/api/workspaces/${triageId}?include=triage`,
      );
      expect(detailWithInclude.status()).toBe(200);
    }
  },
);
