import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { CONTROL_TOKEN_HEADER, CONTROL_TOKEN_STORAGE_KEY } from "../src/controlToken";
import { expect, test } from "./fixtures/test-base";

function readBootstrappedControlToken(root: string): string {
  return fs.readFileSync(path.join(root, "state", "control_token"), "utf8").trim();
}

const SECRET_BODY = { engine: "e2e-settings-token", purpose: "transport", value: "probe" };

test(
  "operator pastes the control token in the settings modal and a guarded browser mutation stops 401ing",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readBootstrappedControlToken(e2eRoot);

    const preRes = await request.post("/api/secrets", { data: SECRET_BODY });
    expect(preRes.status()).toBe(401);

    const fireGuardedMutation = () =>
      page.evaluate(
        async ({ header, key, body }) => {
          const stored = localStorage.getItem(key);
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (stored) headers[header] = stored;
          const res = await fetch("/api/secrets", {
            method: "POST",
            headers,
            body: JSON.stringify(body),
          });
          return res.status;
        },
        { header: CONTROL_TOKEN_HEADER, key: CONTROL_TOKEN_STORAGE_KEY, body: SECRET_BODY },
      );

    await page.goto("/");
    const gear = page.getByTestId("settings-gear");
    await gear.click();
    const modal = page.getByTestId("settings-modal");
    await expect(modal).toBeVisible();
    await expect(page.getByTestId("control-token-status")).toHaveText("No token set.");

    const input = page.getByTestId("control-token-input");
    await input.fill(token);
    await expect(input).toHaveAttribute("type", "password");

    await page.getByTestId("toggle-token-visibility").click();
    await expect(input).toHaveAttribute("type", "text");

    await page.getByTestId("save-token").click();
    await expect(page.getByTestId("control-token-status")).toHaveText("Token set.");

    await page.keyboard.press("Escape");
    await expect(modal).not.toBeVisible();
    await expect(gear).toBeFocused();

    expect(await fireGuardedMutation()).toBe(201);

    await page.reload();
    expect(await fireGuardedMutation()).toBe(201);

    await gear.click();
    await expect(page.getByTestId("control-token-status")).toHaveText("Token set.");
    await page.getByTestId("clear-token").click();
    await expect(page.getByTestId("control-token-status")).toHaveText("No token set.");
    await page.keyboard.press("Escape");
    await expect(modal).not.toBeVisible();

    expect(await fireGuardedMutation()).toBe(401);
  },
);

test(
  "operator records a credential expiry at storage time",
  { tag: "@no-claude" },
  async ({ request, e2eRoot }) => {
    const token = readBootstrappedControlToken(e2eRoot);
    const body = {
      engine: "e2e-settings-token-expiry",
      purpose: "transport",
      value: "probe-with-expiry",
      expires_at: "2099-01-01",
    };

    const createRes = await request.post("/api/secrets", {
      data: body,
      headers: { [CONTROL_TOKEN_HEADER]: token },
    });
    expect(createRes.status()).toBe(201);
    const created = (await createRes.json()) as {
      expires_at: string | null;
      expiry_warned_at: string | null;
    };
    expect(created.expires_at).toBe("2099-01-01");
    expect(created.expiry_warned_at).toBeNull();

    const listRes = await request.get("/api/secrets");
    const { secrets } = (await listRes.json()) as {
      secrets: { name: string; expires_at: string | null }[];
    };
    const stored = secrets.find((s) => s.name === "e2e-settings-token-expiry:transport");
    expect(stored?.expires_at).toBe("2099-01-01");
  },
);

async function fireGuardedMutation(page: Page, token: string | null): Promise<number> {
  return page.evaluate(
    async ({ header, value }) => {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (value) headers[header] = value;
      const res = await fetch("/api/secrets", {
        method: "POST",
        headers,
        body: JSON.stringify({
          engine: "e2e-settings-token-lifecycle",
          purpose: "transport",
          value: "probe",
        }),
      });
      return res.status;
    },
    { header: CONTROL_TOKEN_HEADER, value: token },
  );
}

test(
  "Settings shows the server lifecycle line, separate from the local paste indicator, derived from the server's deadline",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    const localLine = page.getByTestId("control-token-status");
    const serverLine = page.getByTestId("control-token-server-status");
    await expect(localLine).toBeVisible();
    await expect(serverLine).toBeVisible();
    await expect(serverLine).toContainText("active");
    await expect(serverLine).toContainText(/expires in \d+ days?/);
  },
);

test(
  "Extend leaves the token value unchanged, sends no secret, and pushes the deadline out",
  { tag: "@no-claude" },
  async ({ page, request, e2eRoot }) => {
    const token = readBootstrappedControlToken(e2eRoot);
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    await page.getByTestId("control-token-input").fill(token);
    await page.getByTestId("save-token").click();

    const serverLine = page.getByTestId("control-token-server-status");
    await expect(serverLine).toContainText("active");
    const before = (await (await request.get("/api/control-token/status")).json()) as {
      deadline: string | null;
    };

    await page.getByTestId("control-token-extend").click();
    await expect(page.getByTestId("control-token-announcement")).toContainText(/extended/);
    const after = (await (await request.get("/api/control-token/status")).json()) as {
      deadline: string | null;
    };
    expect(after.deadline).not.toBe(before.deadline);
    await expect(serverLine).toContainText("active");

    expect(readBootstrappedControlToken(e2eRoot)).toBe(token);
    expect(await fireGuardedMutation(page, token)).toBe(201);
  },
);

test(
  "Replace states the out-of-band fetch and multi-device sign-out before commit, then rotates the server value and signs the old one out",
  { tag: "@no-claude" },
  async ({ page, e2eRoot }) => {
    const oldToken = readBootstrappedControlToken(e2eRoot);
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    await page.getByTestId("control-token-input").fill(oldToken);
    await page.getByTestId("save-token").click();

    await page.getByTestId("control-token-replace").click();
    const dialog = page.getByTestId("control-token-replace-confirm");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("never sent over the network");
    await expect(dialog).toContainText("including this browser");
    const confirmBtn = dialog.getByRole("button", { name: "Replace token" });
    await expect(confirmBtn).toHaveClass(/bg-btn-reset/);

    await confirmBtn.click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByTestId("control-token-server-status")).toContainText("active");

    await expect.poll(() => readBootstrappedControlToken(e2eRoot)).not.toBe(oldToken);
    const newToken = readBootstrappedControlToken(e2eRoot);
    expect(newToken).not.toBe(oldToken);

    expect(await fireGuardedMutation(page, oldToken)).toBe(403);
    expect(await fireGuardedMutation(page, newToken)).toBe(201);
  },
);

test(
  "Revoke is framed as the destructive action, behind a confirmation, and Cancel makes no request",
  { tag: "@no-claude" },
  async ({ page, e2eRoot }) => {
    const token = readBootstrappedControlToken(e2eRoot);
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    await page.getByTestId("control-token-input").fill(token);
    await page.getByTestId("save-token").click();

    await page.getByTestId("control-token-revoke").click();
    const dialog = page.getByTestId("control-token-revoke-confirm");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("no replacement");
    await expect(dialog).toContainText("including this one");
    const confirmBtn = dialog.getByRole("button", { name: "Revoke token" });
    await expect(confirmBtn).toHaveClass(/bg-btn-destroy/);

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).not.toBeVisible();
    expect(readBootstrappedControlToken(e2eRoot)).toBe(token);
    expect(await fireGuardedMutation(page, token)).toBe(201);
  },
);
