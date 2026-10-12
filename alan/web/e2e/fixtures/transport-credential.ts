import fs from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { TEST_DEFAULT_PROFILE } from "./default-profile";
import { waitForDispatcherIdle } from "./helpers";
import { expect, test } from "./test-base";

const E2E_TRANSPORT_VALUE = "e2e-transport-sentinel";
const SETTLE_TIMEOUT_MS = 30_000;
const HOP_GAP_MS = 300;

async function settleDispatcher(request: APIRequestContext): Promise<void> {
  await waitForDispatcherIdle(request, { timeout: SETTLE_TIMEOUT_MS });
  await new Promise((resolve) => setTimeout(resolve, HOP_GAP_MS));
  await waitForDispatcherIdle(request, { timeout: SETTLE_TIMEOUT_MS });
}

function profileField(name: "owner" | "repo"): string {
  const text = fs.readFileSync(TEST_DEFAULT_PROFILE, "utf8");
  const value = new RegExp(`^${name}:\\s*(\\S+)\\s*$`, "m").exec(text)?.[1];
  if (value === undefined) throw new Error(`the test default profile has no ${name}`);
  return value;
}

function defaultEngineSlug(): string {
  return `${profileField("owner")}/${profileField("repo")}`;
}

function controlHeaders(e2eRoot: string): Record<string, string> {
  const token = fs.readFileSync(path.join(e2eRoot, "state", "control_token"), "utf8").trim();
  return { "X-Alan-Control-Token": token };
}

async function seedTransportCredential(
  request: APIRequestContext,
  e2eRoot: string,
): Promise<void> {
  const res = await request.post("/api/secrets", {
    data: { engine: defaultEngineSlug(), purpose: "transport", value: E2E_TRANSPORT_VALUE },
    headers: controlHeaders(e2eRoot),
  });
  expect(res.status()).toBe(201);
}

export function withDefaultEngineTransportCredential(): void {
  test.beforeEach(async ({ request, e2eRoot }) => {
    await seedTransportCredential(request, e2eRoot);
  });
  test.afterEach(async ({ request, e2eRoot }) => {
    await settleDispatcher(request);
    const res = await request.delete("/api/secrets", {
      data: { engine: defaultEngineSlug(), purpose: "transport" },
      headers: controlHeaders(e2eRoot),
    });
    expect(res.status()).toBe(204);
  });
}
