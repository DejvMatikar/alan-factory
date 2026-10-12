import { expect, test } from "./fixtures/test-base";

test(
  "workspace screen surfaces assumptions alongside requirements with distinct, accessible status treatment",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `e2e-assumptions-${String(Date.now())}`;
    const create = await request.post("/api/workspaces", {
      data: { title, description: "brief refinement assumptions surface" },
    });
    expect(create.ok()).toBeTruthy();
    const { task_id: taskId } = (await create.json()) as { task_id: string };

    await page.route(
      (url) => url.pathname === `/api/workspaces/${taskId}`,
      async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        const response = await route.fetch();
        const json = (await response.json()) as Record<string, unknown>;
        json.requirements = [
          {
            id: "req-1",
            text: "The API returns 200",
            test_ref: "test_api_200",
            status: "PASS",
            merged_pr_url: null,
            withdrawn_by_question_id: null,
          },
          {
            id: "req-2",
            text: "Retries are idempotent",
            test_ref: null,
            status: "PENDING",
            merged_pr_url: null,
            withdrawn_by_question_id: null,
          },
        ];
        json.unproven_clause_ids = ["req-2"];
        json.assumptions = [
          "Assumed the endpoint is idempotent",
          "Assumed retries are safe",
        ];
        await route.fulfill({ response, json });
      },
    );

    await page.goto(`/workspaces/${taskId}`);

    await expect(page.getByRole("heading", { level: 3, name: "Requirements" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: "Assumptions" })).toBeVisible();

    const requirementsList = page.getByTestId("requirements-checklist");
    const assumptionsList = page.getByTestId("assumptions-checklist");
    await expect(requirementsList).toBeVisible();
    await expect(assumptionsList).toBeVisible();
    await expect(requirementsList).toHaveRole("list");
    await expect(assumptionsList).toHaveRole("list");

    await expect(page.getByTestId("assumption-asm-0")).toBeVisible();
    await expect(page.getByTestId("assumption-asm-1")).toBeVisible();
    for (const id of ["asm-0", "asm-1"]) {
      await expect(page.getByTestId(`assumption-${id}`)).toHaveRole("listitem");
    }

    await expect(page.getByLabel("status: open")).toHaveCount(0);

    const passBadge = page.getByLabel("status: pass");
    await expect(passBadge).toHaveClass(/text-green-400/);

    const unprovenBadge = page.getByLabel("status: not mechanically checked");
    await expect(unprovenBadge).toBeVisible();
    await expect(unprovenBadge).toHaveClass(/text-status-warning-fg/);
    await expect(unprovenBadge).not.toHaveClass(/text-text-muted/);
    await expect(page.getByLabel("status: pending")).toHaveCount(0);

    const summary = page.getByTestId("acceptance-checklist-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toHaveText("1 of 2 met (1 never mechanically checked)");
  },
);
