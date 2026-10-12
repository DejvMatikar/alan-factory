import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";
import { seedControlToken } from "./fixtures/seed";

function readControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

function uniqueRepo(prefix: string): { owner: string; repo: string } {
  return { owner: `${prefix}-co`, repo: `${prefix}-${String(Date.now())}` };
}

async function seedTransportSecret(
  request: APIRequestContext,
  token: string,
  slug: string,
  value: string,
): Promise<void> {
  const res = await request.post("/api/secrets", {
    data: { engine: slug, purpose: "transport", value },
    headers: { "X-Alan-Control-Token": token },
  });
  expect(res.status()).toBe(201);
}

test.describe("connect wizard — step 1 (identify)", () => {
  test(
    "fresh-start form: pasting a full repository URL echoes the resolved identity, then advances",
    { tag: "@no-claude" },
    async ({ page }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-url-paste");
      await page.goto("/connect");
      await page
        .getByTestId("connect-repo-url-input")
        .fill(`https://github.com/${owner}/${repo}.git`);
      await expect(page.getByTestId("connect-identity-resolved")).toHaveText(
        `Will connect ${owner}/${repo}`,
      );
      await page.getByTestId("connect-identity-continue").click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}$`));
      await expect(page.getByTestId("step1-identity-detail")).toHaveText(`${owner}/${repo}`);
    },
  );

  test(
    "fresh-start form: a bare owner/repo slug still works (regression)",
    { tag: "@no-claude" },
    async ({ page }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-slug-paste");
      await page.goto("/connect");
      await page.getByTestId("connect-repo-url-input").fill(`${owner}/${repo}`);
      await page.getByTestId("connect-identity-continue").click();
      await expect(page).toHaveURL(new RegExp(`/connect/${owner}/${repo}$`));
      await expect(page.getByTestId("step1-identity-detail")).toHaveText(`${owner}/${repo}`);
    },
  );

  test(
    "fresh-start form: a non-GitHub URL and unparsable text are rejected inline, never advancing",
    { tag: "@no-claude" },
    async ({ page }) => {
      await page.goto("/connect");
      const input = page.getByTestId("connect-repo-url-input");
      const continueButton = page.getByTestId("connect-identity-continue");

      await input.fill("https://gitlab.com/acme/widget");
      await expect(page.getByTestId("connect-identity-error")).toContainText(
        "Only github.com repositories are supported.",
      );
      await expect(continueButton).toBeDisabled();
      await expect(page).toHaveURL(/\/connect$/);

      await input.fill("not a url at all");
      await expect(page.getByTestId("connect-identity-error")).toContainText(
        "Enter a GitHub repository URL or an owner/repo slug.",
      );
      await expect(continueButton).toBeDisabled();
      await expect(page).toHaveURL(/\/connect$/);
    },
  );

  test(
    "Settings credentials surface also accepts a pasted repository URL, not just a bare slug",
    { tag: "@no-claude" },
    async ({ page, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-cred-url");
      const slug = `${owner}/${repo}`;
      await seedControlToken(page, e2eRoot);
      await page.goto("/");
      await page.getByTestId("settings-gear").click();

      await page.getByTestId("credential-engine-input").fill(`https://github.com/${slug}.git`);
      await expect(page.getByTestId("credential-preview")).toContainText(`${slug}:transport`);
      await page.getByTestId("credential-value-input").fill("ghp_e2e_url_placeholder");
      await page.getByTestId("credential-submit").click();
      await expect(page.getByTestId(`credential-row-${slug}:transport`)).toBeVisible();
    },
  );

  test(
    "from a blank slate, direct nav renders identity, credential, and reachability from real checks in forced order",
    { tag: "@no-claude" },
    async ({ page }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-blank");
      await page.goto(`/connect/${owner}/${repo}`);

      await expect(page.getByTestId("step1-identity-status")).toHaveText("pass");
      await expect(page.getByTestId("step1-identity-detail")).toHaveText(`${owner}/${repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("fail");
      await expect(page.getByTestId("step1-credential-remedy")).toContainText("Settings");
      await expect(page.getByTestId("step1-reachability-status")).toHaveText("unknown");
      await expect(page.getByTestId("step1-proposal-status")).toHaveText("not yet checked");
    },
  );

  test(
    "Settings credentials surface lists, adds, and deletes records end-to-end; the value is never echoed",
    { tag: "@no-claude" },
    async ({ page, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-cred-crud");
      const slug = `${owner}/${repo}`;
      await seedControlToken(page, e2eRoot);
      await page.goto("/");
      await page.getByTestId("settings-gear").click();

      await page.getByTestId("credential-engine-input").fill(slug);
      await expect(page.getByTestId("credential-preview")).toContainText(`${slug}:transport`);
      await page.getByTestId("credential-value-input").fill("ghp_e2e_placeholder_value");
      await page.getByTestId("credential-submit").click();

      const row = page.getByTestId(`credential-row-${slug}:transport`);
      await expect(row).toBeVisible();
      await expect(row).not.toContainText("ghp_e2e_placeholder_value");
      await expect(page.locator("body")).not.toContainText("ghp_e2e_placeholder_value");

      await row.getByTestId(`credential-row-${slug}:transport-delete`).click();
      const confirmDialog = page.getByTestId("credential-delete-confirm");
      await expect(confirmDialog).toBeVisible();
      await confirmDialog.getByRole("button", { name: "Delete" }).click();
      await expect(page.getByTestId(`credential-row-${slug}:transport`)).toHaveCount(0);
    },
  );

  test(
    "no control token: reads render fine, the mutating action is blocked with a typed notice pointing at Settings",
    { tag: "@no-claude" },
    async ({ page }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-notoken");
      await page.goto(`/connect/${owner}/${repo}`);

      await expect(page.getByTestId("step1-identity-status")).toHaveText("pass");
      await expect(page.getByTestId("step1-credential-status")).toHaveText("fail");

      await page.getByTestId("step1-footer-primary").click();
      await expect(page.getByTestId("step1-mutation-error")).toBeVisible();
      await expect(page.getByTestId("step1-mutation-error-message")).toContainText("Settings");
      await expect(page.getByTestId("step1-mutation-error-message")).not.toContainText(
        "No credential exists",
      );
    },
  );

  test(
    "abandoning after creating only the credential leaves it retained, with no orphan engine",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const { owner, repo } = uniqueRepo("e2e-connect-abandon");
      const slug = `${owner}/${repo}`;
      await seedControlToken(page, e2eRoot);
      await page.goto("/");
      await page.getByTestId("settings-gear").click();
      await page.getByTestId("credential-engine-input").fill(slug);
      await page.getByTestId("credential-value-input").fill("ghp_abandon_placeholder");
      await page.getByTestId("credential-submit").click();
      await expect(page.getByTestId(`credential-row-${slug}:transport`)).toBeVisible();

      await page.goto("/");

      const secretsRes = await request.get("/api/secrets");
      const { secrets } = (await secretsRes.json()) as { secrets: { name: string }[] };
      expect(secrets.some((s) => s.name === `${slug}:transport`)).toBe(true);

      const engineRes = await request.get(`/api/engines/${owner}/${repo}`);
      expect(engineRes.status()).toBe(404);
    },
  );

  test(
    "per-repo resume re-derives the same state on re-entry, and two repos mid-setup stay independent",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const repoA = uniqueRepo("e2e-connect-resume-a");
      const repoB = uniqueRepo("e2e-connect-resume-b");
      const token = readControlToken(e2eRoot);
      await seedTransportSecret(request, token, `${repoA.owner}/${repoA.repo}`, "ghp_a");

      await page.goto(`/connect/${repoA.owner}/${repoA.repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("pass");

      await page.goto(`/connect/${repoB.owner}/${repoB.repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("fail");

      await page.goto(`/connect/${repoA.owner}/${repoA.repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("pass");
    },
  );

  test(
    "inverted reading: a credential minted for a different repo does not satisfy registration — the 422 names the exact expected record, never a raw error",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      const wrongRepo = uniqueRepo("e2e-connect-inverted-wrong");
      const rightRepo = uniqueRepo("e2e-connect-inverted-right");
      const token = readControlToken(e2eRoot);
      await seedTransportSecret(
        request,
        token,
        `${wrongRepo.owner}/${wrongRepo.repo}`,
        "ghp_wrong_repo",
      );

      await seedControlToken(page, e2eRoot);
      await page.goto(`/connect/${rightRepo.owner}/${rightRepo.repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("fail");

      await page.getByTestId("step1-footer-primary").click();
      await expect(page.getByTestId("step1-mutation-error")).toBeVisible();
      await expect(page.getByTestId("step1-mutation-error-message")).toContainText("Credentials");
      await expect(page.getByTestId("step1-mutation-error-detail")).toContainText(
        `${rightRepo.owner}/${rightRepo.repo}:transport`,
      );
      await expect(page.getByTestId("step1-mutation-error-detail")).not.toContainText(
        `${wrongRepo.owner}/${wrongRepo.repo}`,
      );

      const engineRes = await request.get(`/api/engines/${rightRepo.owner}/${rightRepo.repo}`);
      expect(engineRes.status()).toBe(404);
    },
  );

  test(
    "credential created in Settings, then step 1 registers the engine",
    { tag: "@no-claude" },
    async ({ page, request, e2eRoot }) => {
      test.setTimeout(90_000);
      const { owner, repo } = uniqueRepo("e2e-connect-happy");
      const slug = `${owner}/${repo}`;
      await seedControlToken(page, e2eRoot);
      await page.goto("/");
      await page.getByTestId("settings-gear").click();
      await page.getByTestId("credential-engine-input").fill(slug);
      await page.getByTestId("credential-value-input").fill("ghp_happy_placeholder");
      await page.getByTestId("credential-submit").click();
      await expect(page.getByTestId(`credential-row-${slug}:transport`)).toBeVisible();
      await page.getByTestId("settings-modal-close").click();

      await page.goto(`/connect/${owner}/${repo}`);
      await expect(page.getByTestId("step1-credential-status")).toHaveText("pass");

      await page.getByTestId("step1-footer-primary").click();
      await expect(page.getByTestId("step1-proposal-status")).not.toHaveText(
        "not yet checked",
        { timeout: 60_000 },
      );

      const engineRes = await request.get(`/api/engines/${owner}/${repo}`);
      expect(engineRes.status()).toBe(200);
    },
  );
});
