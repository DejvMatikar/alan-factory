import type { Locator, Page } from "@playwright/test";

import { expectRevealAbove } from "./fixtures/layering";
import { registerGuardEngine } from "./fixtures/seed";
import { expect, test } from "./fixtures/test-base";

const TASK_ID = "layout-task";
const INV_ID = "inv_001";
const STEP_COUNTS = [1, 3, 8] as const;
const CARD_WIDTH_PX = 220;
const MIN_TARGET_PX = 24;
const MIN_GAP_PX = 12;
const FAILED_INDEX = 0;
const LONG_ERROR =
  "claude CLI exited with status 1 while running the security review step: the workspace checkout " +
  "had uncommitted changes that blocked the rebase, so the step aborted before it produced any " +
  "output, and the retry budget for this step was already spent on the two earlier attempts.";
const LONG_ROLE = "security_reviewer_with_a_very_long_role_name_for_truncation";

test.beforeEach(async ({ request, e2eRoot }) => {
  await registerGuardEngine(request, e2eRoot, "e2e-step-card-layout-co");
});

function stepId(index: number): string {
  return `step_${String(index)}`;
}

function hasCardBelow(index: number, count: number): boolean {
  return index % 2 === 0 && index + 1 < count;
}

function taskFor(index: number, count: number) {
  const level = Math.floor(index / 2);
  const previousLevel = level === 0 ? [] : [2 * (level - 1), 2 * (level - 1) + 1].filter((i) => i < count);
  const failed = index === FAILED_INDEX;
  return {
    id: stepId(index),
    type: "io.alan.LLMStep",
    role: failed ? LONG_ROLE : `role_${String(index)}`,
    model: "claude-sonnet-4-5-20250929",
    depends_on: previousLevel.map(stepId),
    status: failed ? "failed" : "success",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:10Z",
    stream_path: null,
    dispatched: [],
    error: failed ? LONG_ERROR : null,
    input_tokens: 1200,
    output_tokens: 3400,
    cache_creation_input_tokens: null,
    cache_read_input_tokens: null,
    total_cost_usd: 0.0123,
    usage_raw: null,
  };
}

async function stubInvocation(page: Page, count: number): Promise<void> {
  const doc = {
    schema_version: "1.0",
    id: INV_ID,
    namespace: `alan.workspace.${TASK_ID}`,
    team: "research",
    status: "failed",
    started_at: "2026-05-12T08:00:00Z",
    completed_at: "2026-05-12T08:00:30Z",
    tasks: Array.from({ length: count }, (_, index) => taskFor(index, count)),
  };
  await page.route(`**/api/workspaces/${TASK_ID}/invocations/${INV_ID}`, (route) =>
    route.fulfill({ json: doc }),
  );
  await page.route(`**/api/workspaces/${TASK_ID}/invocations/${INV_ID}/agents/*/stream`, (route) =>
    route.fulfill({ json: [] }),
  );
}

async function waitForSettledGraph(card: Locator): Promise<void> {
  let previous = "";
  await expect
    .poll(
      async () => {
        const box = JSON.stringify(await card.boundingBox());
        const settled = box === previous;
        previous = box;
        return settled;
      },
      { timeout: 10_000, intervals: [150, 250, 500] },
    )
    .toBe(true);
}

interface CardMetrics {
  id: string;
  left: number;
  top: number;
  right: number;
  bottom: number;
  scale: number;
  offsetWidth: number;
  scrollHeight: number;
  clientHeight: number;
  smallestIconPx: number;
}

async function measureCards(page: Page): Promise<CardMetrics[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('[data-testid^="workflow-node-"][data-status]')).map(
      (el) => {
        const rect = el.getBoundingClientRect();
        const icons = Array.from(el.querySelectorAll<HTMLElement>('[data-testid^="icon-"]'));
        return {
          id: el.dataset.testid ?? "",
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          scale: rect.width / el.offsetWidth,
          offsetWidth: el.offsetWidth,
          scrollHeight: el.scrollHeight,
          clientHeight: el.clientHeight,
          smallestIconPx: Math.min(...icons.flatMap((icon) => [icon.offsetWidth, icon.offsetHeight])),
        };
      },
    ),
  );
}

function intersects(a: CardMetrics, b: CardMetrics): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

function verticalGapPx(above: CardMetrics, below: CardMetrics): number {
  return (below.top - above.bottom) / above.scale;
}

function sameColumn(a: CardMetrics, b: CardMetrics): boolean {
  return Math.abs(a.left - b.left) < 1;
}

function iconLocators(card: Locator): Locator[] {
  return ["icon-output", "icon-prompt", "icon-config"].map((id) => card.getByTestId(id));
}

async function revealIdOf(icon: Locator): Promise<string> {
  const id = await icon.getAttribute("aria-describedby");
  expect(id).not.toBeNull();
  return String(id);
}

async function expectRevealInsideGraph(page: Page, revealId: string, graph: Locator): Promise<void> {
  const box = await graph.boundingBox();
  const reveal = await page.locator(`[id="${revealId}"]`).boundingBox();
  expect(box).not.toBeNull();
  expect(reveal).not.toBeNull();
  expect(reveal!.y).toBeGreaterThanOrEqual(box!.y);
  expect(reveal!.y + reveal!.height).toBeLessThanOrEqual(box!.y + box!.height);
  expect(reveal!.x).toBeGreaterThanOrEqual(box!.x);
  expect(reveal!.x + reveal!.width).toBeLessThanOrEqual(box!.x + box!.width);
}

for (const count of STEP_COUNTS) {
  test.describe(`${String(count)} step(s) on the run full-view page`, { tag: "@no-claude" }, () => {
    test.beforeEach(async ({ page }) => {
      await stubInvocation(page, count);
      await page.goto(`/workspaces/${TASK_ID}/invocations/${INV_ID}`);
      const first = page.getByTestId(`workflow-node-${stepId(0)}`);
      await expect(first).toBeVisible();
      await waitForSettledGraph(first);
    });

    test("cards keep their width, never overlap and never clip their own content", async ({ page }) => {
      const cards = await measureCards(page);
      expect(cards).toHaveLength(count);
      for (const card of cards) {
        expect(card.offsetWidth, `${card.id} width`).toBe(CARD_WIDTH_PX);
        expect(card.scrollHeight, `${card.id} clips its content`).toBeLessThanOrEqual(card.clientHeight);
      }
      for (const [i, a] of cards.entries()) {
        for (const b of cards.slice(i + 1)) {
          expect(intersects(a, b), `${a.id} overlaps ${b.id}`).toBe(false);
          if (sameColumn(a, b)) {
            const [above, below] = a.top < b.top ? [a, b] : [b, a];
            expect(verticalGapPx(above, below), `${above.id} and ${below.id} gap`).toBeGreaterThanOrEqual(
              MIN_GAP_PX,
            );
          }
        }
      }
    });

    test("every icon has a clickable area of at least 24x24", async ({ page }) => {
      const cards = await measureCards(page);
      for (const card of cards) {
        expect(card.smallestIconPx, `${card.id} icon`).toBeGreaterThanOrEqual(MIN_TARGET_PX);
      }
    });

    if (hasCardBelow(FAILED_INDEX, count)) {
      test("a keyboard-focused icon on the tallest card shows its description above the card beneath", async ({
        page,
      }) => {
        const icons = iconLocators(page.getByTestId(`workflow-node-${stepId(FAILED_INDEX)}`));
        for (const icon of icons) {
          await expectRevealAbove(
            page,
            await revealIdOf(icon),
            `[data-testid="workflow-node-${stepId(FAILED_INDEX + 1)}"]`,
            () => icon.focus(),
          );
        }
      });
    }

    test("a keyboard-focused icon on every bottom-most card shows its description inside the graph", async ({
      page,
    }) => {
      const graph = page.getByTestId(`workflow-graph-${INV_ID}`);
      for (let index = 0; index < count; index += 1) {
        if (hasCardBelow(index, count)) continue;
        for (const icon of iconLocators(page.getByTestId(`workflow-node-${stepId(index)}`))) {
          const revealId = await revealIdOf(icon);
          await icon.focus();
          await expect(page.locator(`[id="${revealId}"]`)).not.toHaveClass(/sr-only/);
          await expectRevealInsideGraph(page, revealId, graph);
        }
      }
    });

    test("the failed card has no title and selecting it shows the full error in view", async ({ page }) => {
      const failed = page.getByTestId(`workflow-node-${stepId(FAILED_INDEX)}`);
      await expect(failed).not.toHaveAttribute("title");
      await expect(failed.locator("[title]")).toHaveCount(0);
      await failed.getByRole("button", { name: /^Show activity of / }).click();
      const error = page.getByTestId("stream-node-error");
      await expect(error).toContainText(LONG_ERROR);
      await expect(error).toBeInViewport({ ratio: 1 });
    });
  });
}
