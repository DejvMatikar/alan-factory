import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { seedProjectFeatures } from "../fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

const here = path.dirname(fileURLToPath(import.meta.url));
const envScreensDir = process.env.ALAN_CAPTURE_SCREENS_DIR?.trim();
const outputDir =
  envScreensDir === undefined || envScreensDir === ""
    ? path.resolve(here, "..", "..", "screen-captures")
    : envScreensDir;

interface CaptureRoute {
  readonly name: string;
  readonly route: string;
}

const SCREENS: readonly CaptureRoute[] = [
  { name: "home", route: "/" },
  { name: "build", route: "/build" },
  { name: "explore-architecture", route: "/explore/architecture" },
  { name: "health", route: "/health" },
  { name: "projects", route: "/projects" },
];

test("captures every named screen at desktop and mobile viewports", async (
  { page },
  testInfo,
) => {
  fs.mkdirSync(outputDir, { recursive: true });

  for (const screen of SCREENS) {
    await page.goto(screen.route);
    await page.waitForTimeout(1_000);

    const pngPath = path.join(outputDir, `${screen.name}.${testInfo.project.name}.png`);
    await page.screenshot({ path: pngPath, fullPage: true });

    expect(fs.existsSync(pngPath)).toBeTruthy();
  }
});

test("captures the project detail screen (doc + ledger + propose action) at desktop and mobile viewports", async (
  { page },
  testInfo,
) => {
  fs.mkdirSync(outputDir, { recursive: true });

  const createRes = await page.request.post("/api/projects", {
    data: { title: "Capture Project" },
  });
  const { id: projectId } = (await createRes.json()) as { id: string };

  await page.goto(`/projects/${projectId}`);
  await page.getByTestId("project-doc").waitFor();
  await page.getByTestId("propose-amendment-button").waitFor();

  const pngPath = path.join(outputDir, `project-detail.${testInfo.project.name}.png`);
  await page.screenshot({ path: pngPath, fullPage: true });

  expect(fs.existsSync(pngPath)).toBeTruthy();
});
