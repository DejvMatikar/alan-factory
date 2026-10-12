import type { Page } from "@playwright/test";

import { settleChatSession } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readyProposalFixture(title: string, team = "brainstorming") {
  return {
    status: "ready",
    proposal: {
      team,
      rationale: "r",
      instructions: null,
      scope_audit: [],
      title,
      acceptance_clauses: [],
      counter_case: { against: "a", cheapest_80: "b", cost_estimate: "c" },
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

async function reachSummaryTab(page: Page, beforeSummarize?: () => Promise<void>): Promise<void> {
  await page.goto("/explore/brainstorming");
  await settleChatSession(page);
  await page.getByLabel("Message").fill("Let's scope a brainstorm on the onboarding flow.");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
  await beforeSummarize?.();
  await page.getByTestId("summarize-button").click();
  await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
    timeout: 15_000,
  });
}

test(
  "Dispatch renders the inline proposal panel with rationale, counter-case, and collision verdict before any workspace is created",
  async ({ page }) => {
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await reachSummaryTab(page, async () => {
      await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
        await route.fulfill({
          status: 200,
          json: readyProposalFixture("brainstorm-onboarding"),
        });
      });
    });

    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("dispatch-counter-case")).toBeVisible();
    await expect(page.getByTestId("collision-sentence")).toHaveText(
      "No overlapping work found.",
    );

    expect(workspaceCalls).toBe(0);
  },
);

test(
  "Confirming the panel dispatches the consultant workspace via the atomic create-and-dispatch call, waiver included, and navigates home",
  async ({ page }) => {
    await reachSummaryTab(page, async () => {
      await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
        await route.fulfill({
          status: 200,
          json: readyProposalFixture("brainstorm-onboarding"),
        });
      });
    });

    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 10_000 });

    const dispatchPost = page.waitForRequest(
      (req) => new URL(req.url()).pathname === "/api/workspaces" && req.method() === "POST",
      { timeout: 15_000 },
    );
    await page.getByTestId("dispatch-proposal-confirm").click();
    const req = await dispatchPost;
    const body = req.postDataJSON() as {
      team: string;
      title: string;
      counter_case: { against: string; cheapest_80: string; cost_estimate: string };
    };
    expect(body.team).toBe("brainstorming");
    expect(body.title).toBe("brainstorm-onboarding");
    expect(body.counter_case).toEqual({ against: "a", cheapest_80: "b", cost_estimate: "c" });

    await expect(page).toHaveURL((u) => new URL(u).pathname === "/", { timeout: 15_000 });
  },
);

test(
  "A need_more_info propose reply surfaces its questions inline via QuestionsTab instead of dispatching",
  async ({ page }) => {
    await reachSummaryTab(page, async () => {
      await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
        await route.fulfill({
          status: 200,
          json: { status: "need_more_info", questions: ["Which surface should this cover?"] },
        });
      });
    });

    await expect(page.getByTestId("dispatch-explore-questions")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Which surface should this cover?")).toBeVisible();
  },
);

async function startExploreConversation(page: Page): Promise<void> {
  await page.goto("/explore/brainstorming");
  await settleChatSession(page);
  await page.getByLabel("Message").fill("Let's scope a brainstorm on the onboarding flow.");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByTestId("auto-dispatch-button")).toBeEnabled({ timeout: 10_000 });
}

test(
  "Auto dispatch runs summarize, collision check, and propose in one click and dispatches a workspace",
  async ({ page }) => {
    await startExploreConversation(page);

    await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      await route.fulfill({ status: 200, json: readyProposalFixture("brainstorm-onboarding") });
    });
    let collisionCalls = 0;
    await page.route(/\/api\/collision-check$/, async (route) => {
      collisionCalls += 1;
      await route.fulfill({
        status: 200,
        json: { state: "green", sentence: "No overlapping work found.", match: null },
      });
    });
    const dispatchPost = page.waitForRequest(
      (req) => new URL(req.url()).pathname === "/api/workspaces" && req.method() === "POST",
      { timeout: 20_000 },
    );

    await page.getByTestId("auto-dispatch-button").click();
    const req = await dispatchPost;
    const body = req.postDataJSON() as { team: string; title: string };
    expect(body.team).toBe("brainstorming");
    expect(body.title).toBe("brainstorm-onboarding");

    await expect(page).toHaveURL((u) => new URL(u).pathname === "/", { timeout: 15_000 });
    expect(collisionCalls).toBeGreaterThanOrEqual(1);
  },
);

test(
  "Auto dispatch stops without dispatching when the overlap check is not clear",
  async ({ page }) => {
    await startExploreConversation(page);

    await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      await route.fulfill({ status: 200, json: readyProposalFixture("brainstorm-onboarding") });
    });
    await page.route(/\/api\/collision-check$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: { state: "red", sentence: "Live overlap with a concurrent workspace.", match: null },
      });
    });
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/overlap check/, {
      timeout: 20_000,
    });
    expect(workspaceCalls).toBe(0);
  },
);

test(
  "Auto dispatch can be abandoned with the Stop control before it dispatches",
  async ({ page }) => {
    await startExploreConversation(page);

    await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, () => new Promise(() => undefined));
    let workspaceCalls = 0;
    await page.route(/\/api\/workspaces$/, async (route) => {
      if (route.request().method() === "POST") workspaceCalls += 1;
      await route.continue();
    });

    await page.getByTestId("auto-dispatch-button").click();
    await expect(page.getByTestId("auto-dispatch-stop")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("auto-dispatch-stop").click();
    await expect(page.getByTestId("auto-dispatch-status")).toContainText(/cancelled/);
    expect(workspaceCalls).toBe(0);
  },
);
