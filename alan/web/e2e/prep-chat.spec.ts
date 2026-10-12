import { gotoBuildSession, settleChatSession } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

function readyExploreProposalFixture(title: string, team = "brainstorming") {
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

test(
  "dispatch archives the build chat; the workspace links to it, the read-only page shows the exchange, and the way back returns to the workspace",
  async ({ page }) => {
    await gotoBuildSession(page);
    await page.getByLabel("Message").fill("build the widget feature");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveText("build the widget feature", {
      timeout: 10_000,
    });

    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });

    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 15_000 });

    const dispatchResponse = page.waitForResponse(
      (res) =>
        new URL(res.url()).pathname === "/api/workspaces" &&
        res.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.getByTestId("dispatch-proposal-confirm").click();
    const res = await dispatchResponse;
    const { task_id: taskId } = (await res.json()) as { task_id: string };
    expect(taskId).toBeTruthy();

    await expect(page).toHaveURL((u) => new URL(u).pathname !== "/build", { timeout: 15_000 });

    await page.goto(`/workspaces/${taskId}`);
    const link = page.getByTestId("prep-chat-link");
    await expect(link).toBeVisible({ timeout: 10_000 });
    await expect(link).toHaveText("View preparation chat →");
    await expect(link).not.toHaveText(/archived/i);
    await expect(
      page.getByTestId("workspace-details-aside").getByTestId("prep-chat-link"),
    ).toBeVisible();

    await link.click();
    await expect(page).toHaveURL(`/workspaces/${taskId}/prep-chat`);

    await expect(page.getByTestId("prep-chat-kind")).toHaveText("Build conversation");
    await expect(page.getByTestId("prep-chat-status")).toHaveText("Archived — read-only");
    await expect(page.getByTestId("chat-user")).toHaveText("build the widget feature");

    await expect(page.getByLabel("Message")).toHaveCount(0);
    await expect(page.getByRole("tablist")).toHaveCount(0);

    await page.getByRole("link", { name: /Back to workspace/ }).click();
    await expect(page).toHaveURL(`/workspaces/${taskId}`);
  },
);

test(
  "dispatch archives the explore chat; the workspace links to the named consultant, and the read-only page shows the exchange",
  async ({ page }) => {
    await page.goto("/explore/brainstorming");
    await settleChatSession(page);
    await page.getByLabel("Message").fill("brainstorm onboarding ideas");
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByTestId("chat-user")).toHaveText("brainstorm onboarding ideas", {
      timeout: 10_000,
    });

    await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
    await page.route(/\/api\/explore-chat\/sessions\/[^/]+\/propose$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: readyExploreProposalFixture("brainstorm-onboarding"),
      });
    });
    await page.getByTestId("summarize-button").click();
    await expect(page.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });

    await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 10_000 });

    const dispatchResponse = page.waitForResponse(
      (res) =>
        new URL(res.url()).pathname === "/api/workspaces" &&
        res.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.getByTestId("dispatch-proposal-confirm").click();
    const res = await dispatchResponse;
    const { task_id: taskId } = (await res.json()) as { task_id: string };
    expect(taskId).toBeTruthy();

    await expect(page).toHaveURL((u) => new URL(u).pathname !== "/explore/brainstorming", {
      timeout: 15_000,
    });

    await page.goto(`/workspaces/${taskId}`);
    const link = page.getByTestId("prep-chat-link");
    await expect(link).toBeVisible({ timeout: 10_000 });
    await expect(link).toHaveText("View Brainstorming conversation →");

    await link.click();
    await expect(page).toHaveURL(`/workspaces/${taskId}/prep-chat`);

    await expect(page.getByTestId("prep-chat-kind")).toHaveText(
      "Explore conversation — Brainstorming",
    );
    await expect(page.getByTestId("prep-chat-status")).toHaveText("Archived — read-only");
    await expect(page.getByTestId("chat-user")).toHaveText("brainstorm onboarding ideas");

    await page.getByRole("link", { name: /Back to workspace/ }).click();
    await expect(page).toHaveURL(`/workspaces/${taskId}`);
  },
);
