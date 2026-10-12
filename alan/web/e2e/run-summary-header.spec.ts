import { gotoBuildSession, openExploreFromHome, settleChatSession } from "./fixtures/helpers";
import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

test("run-summary header appears after a turn and survives close/reopen in build chat", async ({
  page,
}) => {
  await gotoBuildSession(page);
  await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });

  await page.getByLabel("Message").fill("build a widget");
  await page.getByRole("button", { name: /Send/ }).click();

  await expect(page.getByTestId("run-summary-header-conversation")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("run-summary-setup-conversation")).toHaveText("opus · xhigh");
  await expect(page.getByTestId("run-summary-cost-conversation")).toHaveText("$0.0012", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("run-summary-output-tokens-conversation")).toHaveText(
    "250 output tokens",
  );
  await expect(page.getByTestId("run-summary-input-tokens-conversation")).toHaveText(
    "100 billed input tokens",
  );

  const url = page.url();
  await page.goto("/");
  await page.goto(url);

  await expect(page.getByTestId("run-summary-header-conversation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("run-summary-cost-conversation")).toHaveText("$0.0012");
});

test("run-summary header appears after a turn and survives close/reopen in explore chat", async ({
  page,
  request,
  e2eRoot,
}) => {
  await registerGuardEngine(request, e2eRoot, "e2e-run-summary-header-co", "run-summary-spare");
  await page.goto("/");
  await openExploreFromHome(page);
  await page.getByTestId("consultant-brainstorming").click();
  await settleChatSession(page);

  await page.getByLabel("Message").fill("let's brainstorm ideas");
  await page.getByRole("button", { name: /Send/ }).click();

  await expect(page.getByTestId("run-summary-header-conversation")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("run-summary-cost-conversation")).toHaveText("$0.0012", {
    timeout: 15_000,
  });

  const url = page.url();
  await page.goto("/");
  await page.goto(url);

  await expect(page.getByTestId("run-summary-header-conversation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("run-summary-cost-conversation")).toHaveText("$0.0012");
});
