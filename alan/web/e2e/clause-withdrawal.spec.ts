import { expect, test } from "./fixtures/test-base";

const LIVE_ID = "req-e2e-live";
const WITHDRAWN_ID = "req-e2e-withdrawn";
const WITHDRAWING_QUESTION_ID = "clause-stall-req-e2e-withdrawn";

test(
  "a withdrawn acceptance clause renders as withdrawn, names its withdrawing question, and is excluded from the unproven summary",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const title = `e2e-clause-withdrawal-${String(Date.now())}`;

    const create = await request.post("/api/workspaces", {
      data: { title, description: "test clause withdrawal rendering" },
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
            id: LIVE_ID,
            text: "the live clause still applies",
            test_ref: null,
            status: "PENDING",
            merged_pr_url: null,
            withdrawn_by_question_id: null,
          },
          {
            id: WITHDRAWN_ID,
            text: "the withdrawn clause no longer applies",
            test_ref: "test_legacy_disabled",
            status: "FAIL",
            merged_pr_url: null,
            withdrawn_by_question_id: WITHDRAWING_QUESTION_ID,
          },
        ];
        json.unproven_clause_ids = [LIVE_ID];
        await route.fulfill({ response, json });
      },
    );

    await page.goto(`/workspaces/${taskId}`);
    const checklist = page.getByTestId("requirements-checklist");
    await expect(checklist).toBeVisible();

    const withdrawnRow = page.getByTestId(`requirement-${WITHDRAWN_ID}`);
    await expect(withdrawnRow).toBeVisible();
    await expect(withdrawnRow.getByLabel("status: withdrawn")).toBeVisible();
    await expect(
      withdrawnRow.getByTestId(`requirement-${WITHDRAWN_ID}-withdrawn-by`),
    ).toContainText(WITHDRAWING_QUESTION_ID);

    const liveRow = page.getByTestId(`requirement-${LIVE_ID}`);
    await expect(liveRow.getByLabel("status: not mechanically checked")).toBeVisible();

    await expect(page.getByTestId("acceptance-checklist-summary")).toContainText(
      "(1 never mechanically checked)",
    );
  },
);
